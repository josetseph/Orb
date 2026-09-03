"""Read-only/backend-owned Firefly III integration for Orb."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.log import get_logger
from app.services.kb_registry import KBContext, kb_registry
from app.services.timing import load_snapshot, log_stage_timing

logger = get_logger("FireflyService")


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_today() -> date:
    return datetime.now(timezone.utc).date()


def _php_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


class FireflyHTTPError(RuntimeError):
    """Firefly API returned an error response (carries the HTTP status code)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FireflyService:
    """Thin typed proxy over the embedded Firefly III REST API."""

    def __init__(self) -> None:
        self.base_url = (settings.FIREFLY_BASE_URL or "").rstrip("/")
        self.runtime_file = settings.FIREFLY_RUNTIME_FILE or ""
        self._scope_lock = asyncio.Lock()
        # Last group activated via the PHP switch script — lets _run_scoped
        # skip the ~1s Laravel bootstrap when the scope hasn't changed.
        self._switched_group_id: int | None = None

    def _load_runtime(self) -> dict[str, Any]:
        if not self.runtime_file:
            return {}
        try:
            return json.loads(Path(self.runtime_file).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _php_paths(self) -> tuple[Path, Path]:
        runtime_path = Path(self.runtime_file or "")
        data_root = runtime_path.parent
        php = data_root / "php" / "php"
        app_dir = data_root / "app"
        return php, app_dir

    def _run_php(self, script: str) -> dict[str, Any]:
        php, app_dir = self._php_paths()
        if not php.is_file():
            raise RuntimeError(f"Embedded PHP binary not found at {php}")
        result = subprocess.run(
            [str(php), "-r", script],
            cwd=str(app_dir),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        raw = (result.stdout or "").strip()
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(stderr or raw or f"PHP exited {result.returncode}")
        json_start = raw.rfind("{")
        if json_start < 0:
            raise RuntimeError(f"PHP script returned no JSON: {raw[:500]}")
        return json.loads(raw[json_start:])

    def _bootstrap_user_id(self) -> int:
        runtime = self._load_runtime()
        user_id = runtime.get("userId")
        if isinstance(user_id, int):
            return user_id
        if isinstance(user_id, str) and user_id.isdigit():
            return int(user_id)
        return 1

    def _php_create_group_script(self, title: str) -> str:
        esc = _php_escape(title)
        user_id = self._bootstrap_user_id()
        return (
            "require 'vendor/autoload.php';"
            "$app = require 'bootstrap/app.php';"
            "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();"
            f"$user = \\FireflyIII\\User::find({user_id});"
            "if (!$user) { fwrite(STDERR, 'Bootstrap user not found'); exit(1); }"
            f"$title = '{esc}';"
            "$group = \\FireflyIII\\Models\\UserGroup::where('title', $title)->first();"
            "if (!$group) {"
            "  $factory = app(\\FireflyIII\\Factory\\UserGroupFactory::class);"
            "  $group = $factory->create(['title' => $title, 'user' => $user]);"
            "}"
            "$ownerRole = \\FireflyIII\\Models\\UserRole::firstOrCreate(['title' => 'owner']);"
            "\\FireflyIII\\Models\\GroupMembership::firstOrCreate(["
            "  'user_id' => $user->id,"
            "  'user_group_id' => $group->id,"
            "  'user_role_id' => $ownerRole->id,"
            "]);"
            "$currency = \\FireflyIII\\Models\\TransactionCurrency::where('code', 'EUR')->first();"
            "if ($currency) {"
            "  $group->currencies()->syncWithoutDetaching([$currency->id => ['group_default' => true]]);"
            "  $user->currencies()->syncWithoutDetaching([$currency->id => ['user_default' => true]]);"
            "}"
            "echo json_encode(['group_id' => $group->id, 'title' => $group->title], JSON_UNESCAPED_SLASHES);"
        )

    def _php_switch_group_script(self, group_id: int) -> str:
        user_id = self._bootstrap_user_id()
        return (
            "require 'vendor/autoload.php';"
            "$app = require 'bootstrap/app.php';"
            "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();"
            f"$user = \\FireflyIII\\User::find({user_id});"
            f"$group = \\FireflyIII\\Models\\UserGroup::find({group_id});"
            "if (!$user || !$group) { fwrite(STDERR, 'User or group not found'); exit(1); }"
            "$user->user_group_id = $group->id;"
            "$user->save();"
            "echo json_encode(['group_id' => $group->id, 'active' => true], JSON_UNESCAPED_SLASHES);"
        )

    def _php_account_ids_for_group_script(self, group_id: int) -> str:
        """List account IDs that belong to a Firefly administration (user group)."""
        return self._php_model_ids_for_group_script("Account", group_id, key="account_ids")

    def _php_model_ids_for_group_script(
        self, model: str, group_id: int, *, key: str = "ids"
    ) -> str:
        """List primary keys for a Firefly model scoped to an administration."""
        allowed = {
            "Account",
            "Category",
            "Budget",
            "Recurrence",
            "Bill",
            "PiggyBank",
            "Tag",
            "Rule",
            "RuleGroup",
            "Webhook",
            "ObjectGroup",
        }
        if model not in allowed:
            raise ValueError(f"Unsupported Firefly model: {model}")
        return (
            "require 'vendor/autoload.php';"
            "$app = require 'bootstrap/app.php';"
            "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();"
            f"$q = \\FireflyIII\\Models\\{model}::query()->where('user_group_id', {int(group_id)});"
            "if (in_array(\\Illuminate\\Database\\Eloquent\\SoftDeletes::class, "
            f"class_uses_recursive(\\FireflyIII\\Models\\{model}::class), true)) "
            "{ $q->whereNull('deleted_at'); }"
            "$ids = $q->pluck('id')->map(fn ($id) => (int) $id)->values()->all();"
            f"echo json_encode(['{key}' => $ids], JSON_UNESCAPED_SLASHES);"
        )

    async def _ids_for_group(self, model: str, group_id: int) -> set[str]:
        try:
            payload = await asyncio.to_thread(
                self._run_php, self._php_model_ids_for_group_script(model, group_id)
            )
            raw = payload.get("ids") if isinstance(payload, dict) else None
            if raw is None and isinstance(payload, dict):
                raw = payload.get("account_ids")
            if not isinstance(raw, list):
                return set()
            return {str(i) for i in raw}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Could not list Firefly %s IDs for group %s: %s", model, group_id, exc
            )
            return set()

    async def _account_ids_for_group(self, group_id: int) -> set[str]:
        return await self._ids_for_group("Account", group_id)

    @staticmethod
    def _filter_api_data_by_ids(payload: Any, allowed: set[str] | None) -> list[dict[str, Any]]:
        rows = []
        for item in FireflyService._as_list(FireflyService._as_dict(payload).get("data")):
            if not isinstance(item, dict):
                continue
            if allowed is not None and str(item.get("id") or "") not in allowed:
                continue
            rows.append(item)
        return rows

    def _php_destroy_group_script(self, group_id: int) -> str:
        """Wipe ledger rows for an administration and delete the UserGroup."""
        user_id = self._bootstrap_user_id()
        gid = int(group_id)
        return (
            "require 'vendor/autoload.php';"
            "$app = require 'bootstrap/app.php';"
            "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();"
            f"$gid = {gid};"
            f"$user = \\FireflyIII\\User::find({user_id});"
            "$group = \\FireflyIII\\Models\\UserGroup::find($gid);"
            "if (!$group) {"
            "  echo json_encode(['destroyed' => false, 'reason' => 'missing'], JSON_UNESCAPED_SLASHES);"
            "  exit(0);"
            "}"
            "$models = ["
            "  \\FireflyIII\\Models\\TransactionJournal::class,"
            "  \\FireflyIII\\Models\\Account::class,"
            "  \\FireflyIII\\Models\\Category::class,"
            "  \\FireflyIII\\Models\\Budget::class,"
            "  \\FireflyIII\\Models\\Bill::class,"
            "  \\FireflyIII\\Models\\PiggyBank::class,"
            "  \\FireflyIII\\Models\\Tag::class,"
            "  \\FireflyIII\\Models\\Rule::class,"
            "  \\FireflyIII\\Models\\RuleGroup::class,"
            "  \\FireflyIII\\Models\\Recurrence::class,"
            "  \\FireflyIII\\Models\\Webhook::class,"
            "  \\FireflyIII\\Models\\ObjectGroup::class,"
            "];"
            "foreach ($models as $class) {"
            "  if (!class_exists($class)) { continue; }"
            "  try {"
            "    $q = $class::query()->where('user_group_id', $gid);"
            "    if (in_array(\\Illuminate\\Database\\Eloquent\\SoftDeletes::class, "
            "        class_uses_recursive($class), true)) {"
            "      $q->withTrashed()->forceDelete();"
            "    } else {"
            "      $q->delete();"
            "    }"
            "  } catch (Throwable $e) { /* continue */ }"
            "}"
            "\\FireflyIII\\Models\\GroupMembership::where('user_group_id', $gid)->delete();"
            "if ($user && (int) $user->user_group_id === $gid) {"
            "  $fallback = \\FireflyIII\\Models\\UserGroup::where('id', '!=', $gid)->orderBy('id')->first();"
            "  $user->user_group_id = $fallback ? $fallback->id : null;"
            "  $user->save();"
            "}"
            "try { $group->currencies()->detach(); } catch (Throwable $e) {}"
            "$group->delete();"
            "echo json_encode(['destroyed' => true, 'group_id' => $gid], JSON_UNESCAPED_SLASHES);"
        )

    def _php_update_group_title_script(self, group_id: int, title: str) -> str:
        esc = _php_escape(title)
        return (
            "require 'vendor/autoload.php';"
            "$app = require 'bootstrap/app.php';"
            "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();"
            f"$group = \\FireflyIII\\Models\\UserGroup::find({group_id});"
            "if (!$group) { fwrite(STDERR, 'Group not found'); exit(1); }"
            f"$group->title = '{esc}';"
            "$group->save();"
            "echo json_encode(['group_id' => $group->id, 'title' => $group->title], JSON_UNESCAPED_SLASHES);"
        )

    def _group_title_for_kb(self, kb: KBContext) -> str:
        return f"Orb: {kb.name}"

    async def ensure_kb_scope(self, kb: KBContext) -> int:
        """Resolve or create the Firefly administration for this KB and activate it."""
        async with self._scope_lock:
            return await self._activate_scope_locked(kb)

    async def sync_kb_group_title(self, kb_id: str, new_name: str) -> None:
        meta = kb_registry.get_metadata(kb_id) or {}
        group_id = meta.get("firefly_group_id")
        if not isinstance(group_id, int) or group_id <= 0:
            return
        title = f"Orb: {new_name}"
        await asyncio.to_thread(
            self._run_php,
            self._php_update_group_title_script(group_id, title),
        )
        kb_registry.set_firefly_group(kb_id, group_id, title)

    async def destroy_kb_administration(self, kb: KBContext) -> dict[str, Any]:
        """Delete the Firefly UserGroup and ledger for this KB, then detach mapping."""
        meta = kb_registry.get_metadata(kb.kb_id) or {}
        group_id = meta.get("firefly_group_id")
        if isinstance(group_id, str) and group_id.isdigit():
            group_id = int(group_id)
        if not isinstance(group_id, int) or group_id <= 0:
            kb_registry.detach_firefly_group(kb.kb_id)
            return {"destroyed": False, "reason": "no_group"}

        async with self._scope_lock:
            if self._switched_group_id == group_id:
                self._switched_group_id = None
            try:
                result = await asyncio.to_thread(
                    self._run_php, self._php_destroy_group_script(group_id)
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Firefly destroy admin failed for KB %s group %s: %s",
                    kb.kb_id,
                    group_id,
                    exc,
                )
                kb_registry.detach_firefly_group(kb.kb_id)
                return {"destroyed": False, "reason": str(exc), "group_id": group_id}

        kb_registry.detach_firefly_group(kb.kb_id)
        # Drop cached runtime default group if it pointed at this administration.
        try:
            runtime = self._load_runtime()
            if runtime.get("groupId") == group_id and self.runtime_file:
                runtime.pop("groupId", None)
                Path(self.runtime_file).write_text(
                    json.dumps(runtime, indent=2), encoding="utf-8"
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Could not update Firefly runtime after destroy: %s", exc)

        logger.info(
            "Destroyed Firefly administration %s for KB '%s'", group_id, kb.kb_id
        )
        return result if isinstance(result, dict) else {"destroyed": True, "group_id": group_id}

    def _token(self) -> str | None:
        env_token = settings.FIREFLY_API_TOKEN
        if env_token:
            return env_token
        runtime = self._load_runtime()
        token = runtime.get("apiToken")
        return token if isinstance(token, str) and token.strip() else None

    def _headers(self) -> dict[str, str]:
        token = self._token()
        headers = {
            "Accept": "application/vnd.api+json, application/json",
            # Firefly rejects POSTs with an empty Content-Type (415).
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(45.0),
        )

    @staticmethod
    def _is_json_content_type(content_type: str) -> bool:
        lowered = (content_type or "").lower()
        return (
            "application/json" in lowered
            or "application/vnd.api+json" in lowered
            or "+json" in lowered
        )

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.base_url:
            raise RuntimeError("Firefly base URL is not configured")
        # Ensure body-less mutating requests still send JSON Content-Type + `{}`.
        upper = method.upper()
        if upper in {"POST", "PUT", "PATCH"} and "json" not in kwargs and "content" not in kwargs and "data" not in kwargs:
            kwargs["json"] = {}
        # Stamp the active KB administration onto every request when scoped.
        active_group = getattr(self, "_active_group_id", None)
        if isinstance(active_group, int) and active_group > 0:
            params = dict(kwargs.get("params") or {})
            params.setdefault("user_group_id", active_group)
            kwargs["params"] = params
        async with self._client() as client:
            response = await client.request(method, path, **kwargs)
            if response.is_error:
                detail = response.text
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        detail = (
                            payload.get("message")
                            or payload.get("error")
                            or json.dumps(payload.get("errors") or payload)
                        )
                except Exception:
                    pass
                raise FireflyHTTPError(
                    f"Firefly {method} {path} failed ({response.status_code}): {detail}",
                    status_code=response.status_code,
                )
            # 204 / empty bodies are common for enable/primary currency endpoints.
            if response.status_code == 204 or not (response.content or b"").strip():
                return None
            content_type = response.headers.get("content-type", "")
            if self._is_json_content_type(content_type):
                return response.json()
            # Firefly sometimes omits/changes content-type; try JSON anyway.
            text = response.text
            if text and text.lstrip()[:1] in "{[":
                try:
                    return response.json()
                except Exception:
                    pass
            return text

    _CURRENCY_META: dict[str, dict[str, str]] = {
        "USD": {"name": "US Dollar", "symbol": "$"},
        "EUR": {"name": "Euro", "symbol": "€"},
        "GBP": {"name": "British Pound", "symbol": "£"},
        "GHS": {"name": "Ghanaian Cedi", "symbol": "GH₵"},
        "NGN": {"name": "Nigerian Naira", "symbol": "₦"},
        "CAD": {"name": "Canadian Dollar", "symbol": "CA$"},
        "AUD": {"name": "Australian Dollar", "symbol": "A$"},
        "JPY": {"name": "Japanese Yen", "symbol": "¥"},
        "CHF": {"name": "Swiss Franc", "symbol": "CHF"},
        "INR": {"name": "Indian Rupee", "symbol": "₹"},
        "KES": {"name": "Kenyan Shilling", "symbol": "KSh"},
        "ZAR": {"name": "South African Rand", "symbol": "R"},
    }

    async def _ensure_currency_enabled(self, code: str) -> None:
        """Enable (or create) a currency so it can become primary."""
        meta = self._CURRENCY_META.get(code, {"name": code, "symbol": code})
        try:
            await self._request("POST", f"/api/v1/currencies/{code}/enable")
            return
        except RuntimeError as exc:
            message = str(exc).lower()
            if "404" not in message and "not found" not in message:
                # Already enabled / other non-fatal — continue to primary.
                return

        try:
            await self._request(
                "POST",
                "/api/v1/currencies",
                json={
                    "code": code,
                    "name": meta["name"],
                    "symbol": meta["symbol"],
                    "decimal_places": 2,
                    "enabled": True,
                    "primary": False,
                },
            )
        except RuntimeError as exc:
            message = str(exc).lower()
            if "422" in message or "already" in message:
                await self._request("POST", f"/api/v1/currencies/{code}/enable")
                return
            raise

    def _normalize_account(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = item.get("attributes", {}) if isinstance(item, dict) else {}
        attrs = self._as_dict(attrs)
        account_type = attrs.get("type") or "unknown"
        balance = _as_float(
            attrs.get("current_balance")
            or attrs.get("current_balance_native")
            or attrs.get("current_balance_with_native")
            or attrs.get("balance")
            or attrs.get("virtual_balance")
        )
        # Expense/revenue accounts often expose spent/earned under other keys.
        if balance == 0.0 and account_type in {"expense", "revenue"}:
            balance = abs(
                _as_float(
                    attrs.get("current_debt")
                    or attrs.get("spent")
                    or attrs.get("difference")
                )
            )
        return {
            "id": str(item.get("id")),
            "name": attrs.get("name") or "Untitled account",
            "account_type": account_type,
            "opening_balance": _as_float(attrs.get("opening_balance")),
            "balance": balance,
            "currency": attrs.get("currency_code")
            or attrs.get("native_currency_code")
            or attrs.get("currency_symbol"),
            "archived": bool(attrs.get("active") is False),
        }

    async def create_account(
        self,
        kb: KBContext,
        *,
        name: str,
        account_type: str,
        opening_balance: float = 0.0,
        currency_code: str | None = None,
    ) -> dict[str, Any]:
        account_type = (account_type or "asset").strip().lower()
        if account_type == "liabilities":
            account_type = "liability"
        allowed = {"asset", "expense", "revenue", "liability", "cash"}
        if account_type not in allowed:
            raise ValueError(f"account_type must be one of: {', '.join(sorted(allowed))}")
        name = (name or "").strip()
        if not name:
            raise ValueError("Account name is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {
                "name": name,
                "type": account_type,
                "active": True,
                "include_net_worth": True,
            }
            if currency_code:
                body["currency_code"] = currency_code.strip().upper()
            # Firefly requires account_role for asset accounts even with a zero opening balance.
            if account_type == "asset":
                body["account_role"] = "defaultAsset"
                if opening_balance:
                    body["opening_balance"] = f"{opening_balance:.2f}"
                    body["opening_balance_date"] = datetime.now(timezone.utc).isoformat()
            if account_type == "liability":
                body["liability_type"] = "debt"
                body["liability_direction"] = "debit"
                body["interest"] = "0"
                body["interest_period"] = "monthly"
                if opening_balance:
                    body["opening_balance"] = f"{abs(opening_balance):.2f}"
                    body["opening_balance_date"] = datetime.now(timezone.utc).isoformat()
            payload = await self._request("POST", "/api/v1/accounts", json=body)
            data = self._as_dict(self._as_dict(payload).get("data"))
            if not data:
                raise RuntimeError("Firefly created the account but returned an empty payload")
            return self._normalize_account(data)

        return await self._run_scoped(kb, _create)

    async def create_transaction(
        self,
        kb: KBContext,
        *,
        description: str,
        amount: float,
        tx_type: str,
        account_id: str,
        date_value: str | None = None,
        counterparty_name: str | None = None,
        transfer_account_id: str | None = None,
        category: str | None = None,
        budget_id: str | None = None,
        currency_code: str | None = None,
    ) -> list[dict[str, Any]]:
        tx_type = (tx_type or "withdrawal").strip().lower()
        if tx_type not in {"withdrawal", "deposit", "transfer"}:
            raise ValueError("type must be withdrawal, deposit, or transfer")
        description = (description or "").strip()
        if not description:
            raise ValueError("description is required")
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        account_id = str(account_id or "").strip()
        if not account_id:
            raise ValueError("account_id is required")

        when = date_value or datetime.now(timezone.utc).isoformat()
        split: dict[str, Any] = {
            "type": tx_type,
            "date": when if "T" in when else f"{when}T12:00:00+00:00",
            "amount": f"{abs(amount):.2f}",
            "description": description,
        }
        if category:
            split["category_name"] = category.strip()
        if budget_id and tx_type == "withdrawal":
            split["budget_id"] = str(budget_id)

        async def _create(group_id: int) -> list[dict[str, Any]]:
            accounts = await self._list_accounts_unlocked(group_id)
            by_id = {str(a.get("id")): a for a in accounts}
            source = by_id.get(account_id) or {}
            if not source:
                raise ValueError(
                    "Account not found in this knowledge base's finance scope. "
                    "Create accounts under this vault — they are not shared across vaults."
                )
            resolved_currency = (
                (currency_code or "").strip().upper()
                or (source.get("currency") or "").strip().upper()
            )
            if not resolved_currency:
                primary = await self._request("GET", "/api/v1/currencies/primary")
                attrs = self._as_dict(
                    self._as_dict(self._as_dict(primary).get("data")).get("attributes")
                )
                resolved_currency = (attrs.get("code") or "").strip().upper()
            if resolved_currency:
                split["currency_code"] = resolved_currency

            def _match_account(name: str, allowed_types: set[str]) -> str | None:
                needle = (name or "").strip().lower()
                if not needle:
                    return None
                for row in accounts:
                    if (row.get("account_type") or "").lower() not in allowed_types:
                        continue
                    if (row.get("name") or "").strip().lower() == needle:
                        return str(row.get("id"))
                return None

            if tx_type == "withdrawal":
                split["source_id"] = account_id
                if transfer_account_id:
                    split["destination_id"] = str(transfer_account_id)
                else:
                    matched = _match_account(
                        counterparty_name or "", {"expense", "cash"}
                    )
                    if matched:
                        split["destination_id"] = matched
                    else:
                        split["destination_name"] = (
                            counterparty_name or "Cash expense"
                        ).strip()
            elif tx_type == "deposit":
                split["destination_id"] = account_id
                if transfer_account_id:
                    split["source_id"] = str(transfer_account_id)
                else:
                    matched = _match_account(
                        counterparty_name or "", {"revenue"}
                    )
                    if matched:
                        split["source_id"] = matched
                    else:
                        split["source_name"] = (counterparty_name or "Income").strip()
            else:
                dest = str(transfer_account_id or "").strip()
                if not dest:
                    raise ValueError("transfer_account_id is required for transfers")
                split["source_id"] = account_id
                split["destination_id"] = dest

            payload = await self._request(
                "POST",
                "/api/v1/transactions",
                json={
                    "transactions": [split],
                    "error_if_duplicate_hash": False,
                    "apply_rules": True,
                },
            )
            data = self._as_dict(self._as_dict(payload).get("data"))
            return self._normalize_transaction_group(data)

        return await self._run_scoped(kb, _create)

    async def delete_transaction(self, kb: KBContext, transaction_id: str) -> None:
        group_id = str(transaction_id or "").split(":", 1)[0].strip()
        if not group_id:
            raise ValueError("transaction id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/transactions/{group_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_budget(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes") if isinstance(item, dict) else None)
        spent_rows = attrs.get("spent") if isinstance(attrs.get("spent"), list) else []
        spent_total = sum(_as_float(self._as_dict(row).get("sum")) for row in spent_rows)
        return {
            "id": str(item.get("id")),
            "name": attrs.get("name") or "Untitled budget",
            "active": bool(attrs.get("active", True)),
            "spent": abs(spent_total),
            "currency": (
                self._as_dict(spent_rows[0]).get("currency_code") if spent_rows else None
            ),
            "auto_budget_amount": _as_float(attrs.get("auto_budget_amount")),
            "auto_budget_period": attrs.get("auto_budget_period"),
            "notes": attrs.get("notes"),
        }

    async def list_budgets(self, kb: KBContext, *, days: int = 30) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            end = _iso_today()
            start = end - timedelta(days=max(days, 1) - 1)
            allowed = await self._ids_for_group("Budget", group_id)
            payload = await self._request(
                "GET",
                "/api/v1/budgets",
                params={"limit": 100, "start": start.isoformat(), "end": end.isoformat()},
            )
            rows = []
            for item in self._filter_api_data_by_ids(payload, allowed):
                rows.append(self._normalize_budget(item))
            return rows

        return await self._run_scoped(kb, _list)

    async def create_budget(
        self,
        kb: KBContext,
        *,
        name: str,
        amount: float | None = None,
        currency_code: str | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Budget name is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {"name": name, "active": True}
            if amount and amount > 0:
                body["auto_budget_type"] = "reset"
                body["auto_budget_amount"] = f"{amount:.2f}"
                body["auto_budget_period"] = "monthly"
                if currency_code:
                    body["auto_budget_currency_code"] = currency_code.strip().upper()
            payload = await self._request("POST", "/api/v1/budgets", json=body)
            data = self._as_dict(self._as_dict(payload).get("data"))
            budget = self._normalize_budget(data)
            if amount and amount > 0 and budget.get("id"):
                end = _iso_today()
                start = end.replace(day=1)
                try:
                    await self._request(
                        "POST",
                        f"/api/v1/budgets/{budget['id']}/limits",
                        json={
                            "budget_id": str(budget["id"]),
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "amount": f"{amount:.2f}",
                            **(
                                {"currency_code": currency_code.strip().upper()}
                                if currency_code
                                else {}
                            ),
                        },
                    )
                except RuntimeError:
                    # Auto-budget may already create a limit; budget itself still exists.
                    logger.warning("Could not create budget limit for %s", budget["id"])
            return budget

        return await self._run_scoped(kb, _create)

    async def report(
        self,
        kb: KBContext,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        async def _report(group_id: int) -> dict[str, Any]:
            end_day = date.fromisoformat(end) if end else _iso_today()
            start_day = (
                date.fromisoformat(start)
                if start
                else (end_day - timedelta(days=29))
            )
            params = {
                "start": start_day.isoformat(),
                "end": end_day.isoformat(),
                "user_group_id": group_id,
            }
            basic = await self._request("GET", "/api/v1/summary/basic", params=params)
            category_chart = await self._request(
                "GET", "/api/v1/chart/category/overview", params=params
            )
            budget_chart = await self._request(
                "GET", "/api/v1/chart/budget/overview", params=params
            )
            balance_chart = await self._request(
                "GET",
                "/api/v1/chart/balance/balance",
                params={**params, "period": "1D"},
            )
            accounts = await self._list_accounts_unlocked(group_id)
            # Charts/summary APIs are often still user-wide — keep only this admin's series.
            cat_ids = await self._ids_for_group("Category", group_id)
            budget_ids = await self._ids_for_group("Budget", group_id)
            cat_names = {
                str(row.get("name") or "").strip().lower()
                for row in await self._list_scoped_resources(
                    group_id,
                    model="Category",
                    path="/api/v1/categories",
                    normalize=self._normalize_category,
                )
            }
            budget_names = {
                str(row.get("name") or "").strip().lower()
                for row in await self._list_scoped_resources(
                    group_id,
                    model="Budget",
                    path="/api/v1/budgets",
                    normalize=self._normalize_budget,
                    params={
                        "limit": 100,
                        "start": start_day.isoformat(),
                        "end": end_day.isoformat(),
                    },
                )
            }
            account_names = {
                str(a.get("name") or "").strip().lower() for a in accounts
            }
            return {
                "start": start_day.isoformat(),
                "end": end_day.isoformat(),
                "basic": self._filter_summary_basic(basic, accounts),
                "category_chart": self._filter_chart_series(
                    category_chart, allowed_ids=cat_ids, allowed_labels=cat_names
                ),
                "budget_chart": self._filter_chart_series(
                    budget_chart, allowed_ids=budget_ids, allowed_labels=budget_names
                ),
                "balance_chart": self._filter_chart_series(
                    balance_chart, allowed_labels=account_names
                ),
                "accounts": accounts,
                "kb_id": kb.kb_id,
                "kb_name": kb.name,
            }

        return await self._run_scoped(kb, _report)

    @staticmethod
    def _filter_chart_series(
        chart: Any,
        *,
        allowed_ids: set[str] | None = None,
        allowed_labels: set[str] | None = None,
    ) -> Any:
        """Drop chart series that belong to another Firefly administration."""

        def _keep(row: dict[str, Any]) -> bool:
            rid = str(row.get("id") or row.get("key") or "").strip()
            label = str(row.get("label") or row.get("name") or "").strip().lower()
            id_ok = allowed_ids is None or (bool(rid) and rid in allowed_ids)
            label_ok = allowed_labels is None or (bool(label) and label in allowed_labels)
            if allowed_ids is not None and allowed_labels is not None:
                return id_ok or label_ok
            if allowed_ids is not None:
                return id_ok
            return label_ok

        if isinstance(chart, list):
            return [item for item in chart if isinstance(item, dict) and _keep(item)]
        if isinstance(chart, dict) and isinstance(chart.get("data"), list):
            filtered = [
                item for item in chart["data"] if isinstance(item, dict) and _keep(item)
            ]
            return {**chart, "data": filtered}
        return chart

    @staticmethod
    def _filter_summary_basic(basic: Any, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        """Prefer scoped account balances over Firefly's user-wide summary keys."""
        if not isinstance(basic, dict):
            basic = {}
        currency = None
        balance = 0.0
        for acct in accounts:
            if (acct.get("type") or "") != "asset":
                continue
            currency = currency or acct.get("currency")
            balance += float(acct.get("balance") or 0)
        scoped: dict[str, Any] = {
            "balance-in-vault": {
                "title": "Balance (this vault)",
                "value_parsed": round(balance, 2),
                "currency_code": currency,
            },
            "asset-accounts": {
                "title": "Asset accounts",
                "value_parsed": sum(
                    1 for a in accounts if (a.get("type") or "") == "asset"
                ),
            },
        }
        for key, value in basic.items():
            if not isinstance(key, str):
                continue
            lower = key.lower()
            if any(tok in lower for tok in ("balance", "spent", "earned", "net", "left")):
                continue
            scoped[key] = value
        return scoped

    def _resource_rows(self, payload: Any) -> list[dict[str, Any]]:
        return [item for item in self._as_list(self._as_dict(payload).get("data")) if isinstance(item, dict)]

    async def _list_scoped_resources(
        self,
        group_id: int,
        *,
        model: str,
        path: str,
        normalize,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """GET a Firefly collection and keep only rows for this administration."""
        allowed = await self._ids_for_group(model, group_id)
        payload = await self._request(
            "GET", path, params=params if params is not None else {"limit": 100}
        )
        return [
            normalize(item)
            for item in self._filter_api_data_by_ids(payload, allowed)
        ]

    def _normalize_category(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "name": attrs.get("name") or "Untitled category",
            "notes": attrs.get("notes"),
        }

    async def list_categories(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="Category",
                path="/api/v1/categories",
                normalize=self._normalize_category,
            )

        return await self._run_scoped(kb, _list)

    async def create_category(self, kb: KBContext, *, name: str, notes: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Category name is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {"name": name}
            if notes:
                body["notes"] = notes
            payload = await self._request("POST", "/api/v1/categories", json=body)
            return self._normalize_category(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_category(self, kb: KBContext, category_id: str) -> None:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise ValueError("category id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/categories/{category_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_bill(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "name": attrs.get("name") or "Untitled bill",
            "amount_min": _as_float(attrs.get("amount_min")),
            "amount_max": _as_float(attrs.get("amount_max")),
            "currency": attrs.get("currency_code"),
            "repeat_freq": attrs.get("repeat_freq"),
            "next_expected_match": attrs.get("next_expected_match"),
            "active": bool(attrs.get("active", True)),
            "paid": bool(attrs.get("paid", False)),
            "notes": attrs.get("notes"),
        }

    async def list_bills(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            end = _iso_today()
            start = end - timedelta(days=30)
            return await self._list_scoped_resources(
                group_id,
                model="Bill",
                path="/api/v1/bills",
                normalize=self._normalize_bill,
                params={"limit": 100, "start": start.isoformat(), "end": end.isoformat()},
            )

        return await self._run_scoped(kb, _list)

    async def create_bill(
        self,
        kb: KBContext,
        *,
        name: str,
        amount: float,
        repeat_freq: str = "monthly",
        date_value: str | None = None,
        currency_code: str | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Bill name is required")
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        freq = (repeat_freq or "monthly").strip().lower()
        if freq not in {"weekly", "monthly", "quarterly", "half-year", "yearly"}:
            raise ValueError("repeat_freq must be weekly, monthly, quarterly, half-year, or yearly")
        when = date_value or datetime.now(timezone.utc).isoformat()
        if "T" not in when:
            when = f"{when}T12:00:00+00:00"
        amount_str = f"{amount:.2f}"

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {
                "name": name,
                "amount_min": amount_str,
                "amount_max": amount_str,
                "date": when,
                "repeat_freq": freq,
                "active": True,
                "skip": 0,
            }
            if currency_code:
                body["currency_code"] = currency_code.strip().upper()
            payload = await self._request("POST", "/api/v1/bills", json=body)
            return self._normalize_bill(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_bill(self, kb: KBContext, bill_id: str) -> None:
        bill_id = str(bill_id or "").strip()
        if not bill_id:
            raise ValueError("bill id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/bills/{bill_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_piggy(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "name": attrs.get("name") or "Untitled piggy bank",
            "current_amount": _as_float(attrs.get("current_amount")),
            "target_amount": _as_float(attrs.get("target_amount")),
            "percentage": _as_float(attrs.get("percentage")),
            "currency": attrs.get("currency_code"),
            "start_date": attrs.get("start_date"),
            "target_date": attrs.get("target_date"),
            "notes": attrs.get("notes"),
            "active": bool(attrs.get("active", True)),
        }

    async def list_piggy_banks(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="PiggyBank",
                path="/api/v1/piggy-banks",
                normalize=self._normalize_piggy,
            )

        return await self._run_scoped(kb, _list)

    async def create_piggy_bank(
        self,
        kb: KBContext,
        *,
        name: str,
        account_id: str,
        target_amount: float,
        current_amount: float = 0.0,
        start_date: str | None = None,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        account_id = str(account_id or "").strip()
        if not name:
            raise ValueError("Piggy bank name is required")
        if not account_id:
            raise ValueError("account_id is required")
        if target_amount <= 0:
            raise ValueError("target_amount must be greater than zero")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {
                "name": name,
                "target_amount": f"{target_amount:.2f}",
                "start_date": start_date or _iso_today().isoformat(),
                "accounts": [
                    {
                        "account_id": account_id,
                        "id": account_id,
                        "current_amount": f"{max(current_amount, 0):.2f}",
                    }
                ],
                "account_id": account_id,
            }
            if target_date:
                body["target_date"] = target_date
            payload = await self._request("POST", "/api/v1/piggy-banks", json=body)
            return self._normalize_piggy(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_piggy_bank(self, kb: KBContext, piggy_id: str) -> None:
        piggy_id = str(piggy_id or "").strip()
        if not piggy_id:
            raise ValueError("piggy bank id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/piggy-banks/{piggy_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_tag(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "tag": attrs.get("tag") or attrs.get("name") or "tag",
            "date": attrs.get("date"),
            "description": attrs.get("description"),
        }

    async def list_tags(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="Tag",
                path="/api/v1/tags",
                normalize=self._normalize_tag,
            )

        return await self._run_scoped(kb, _list)

    async def create_tag(
        self,
        kb: KBContext,
        *,
        tag: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        tag = (tag or "").strip()
        if not tag:
            raise ValueError("Tag is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {"tag": tag}
            if description:
                body["description"] = description
            payload = await self._request("POST", "/api/v1/tags", json=body)
            return self._normalize_tag(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_tag(self, kb: KBContext, tag_id: str) -> None:
        tag_id = str(tag_id or "").strip()
        if not tag_id:
            raise ValueError("tag id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/tags/{tag_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_recurrence(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        txs = attrs.get("transactions") if isinstance(attrs.get("transactions"), list) else []
        first_tx = self._as_dict(txs[0]) if txs else {}
        reps = attrs.get("repetitions") if isinstance(attrs.get("repetitions"), list) else []
        first_rep = self._as_dict(reps[0]) if reps else {}
        return {
            "id": str(item.get("id")),
            "title": attrs.get("title") or "Untitled recurrence",
            "type": attrs.get("type") or first_tx.get("type"),
            "description": attrs.get("description") or first_tx.get("description"),
            "amount": _as_float(first_tx.get("amount")),
            "currency": first_tx.get("currency_code"),
            "first_date": attrs.get("first_date"),
            "repeat_until": attrs.get("repeat_until"),
            "active": bool(attrs.get("active", True)),
            "repetition_type": first_rep.get("type"),
            "repetition_moment": first_rep.get("moment"),
            "source_name": first_tx.get("source_name"),
            "destination_name": first_tx.get("destination_name"),
        }

    async def list_recurrences(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="Recurrence",
                path="/api/v1/recurrences",
                normalize=self._normalize_recurrence,
            )

        return await self._run_scoped(kb, _list)

    async def create_recurrence(
        self,
        kb: KBContext,
        *,
        title: str,
        amount: float,
        tx_type: str,
        source_id: str,
        destination_id: str,
        description: str | None = None,
        first_date: str | None = None,
        repeat_freq: str = "monthly",
    ) -> dict[str, Any]:
        title = (title or "").strip()
        tx_type = (tx_type or "withdrawal").strip().lower()
        source_id = str(source_id or "").strip()
        destination_id = str(destination_id or "").strip()
        if not title:
            raise ValueError("title is required")
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        if tx_type not in {"withdrawal", "deposit", "transfer"}:
            raise ValueError("type must be withdrawal, deposit, or transfer")
        if not source_id or not destination_id:
            raise ValueError("source_id and destination_id are required")
        freq = (repeat_freq or "monthly").strip().lower()
        if freq not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValueError("repeat_freq must be daily, weekly, monthly, or yearly")
        start = first_date or (_iso_today() + timedelta(days=1)).isoformat()
        moment = ""
        if freq == "weekly":
            moment = str(date.fromisoformat(start).isoweekday())
        elif freq == "monthly":
            moment = str(date.fromisoformat(start).day)
        elif freq == "yearly":
            moment = start

        async def _create(_group_id: int) -> dict[str, Any]:
            body = {
                "type": tx_type,
                "title": title,
                "description": description or title,
                "first_date": start,
                "repeat_until": None,
                "nr_of_repetitions": None,
                "apply_rules": True,
                "active": True,
                "repetitions": [{"type": freq, "moment": moment, "skip": 0, "weekend": 1}],
                "transactions": [
                    {
                        "description": description or title,
                        "amount": f"{amount:.2f}",
                        "source_id": source_id,
                        "destination_id": destination_id,
                    }
                ],
            }
            payload = await self._request("POST", "/api/v1/recurrences", json=body)
            return self._normalize_recurrence(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_recurrence(self, kb: KBContext, recurrence_id: str) -> None:
        recurrence_id = str(recurrence_id or "").strip()
        if not recurrence_id:
            raise ValueError("recurrence id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/recurrences/{recurrence_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_rule_group(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "title": attrs.get("title") or "Untitled rule group",
            "description": attrs.get("description"),
            "order": attrs.get("order"),
            "active": bool(attrs.get("active", True)),
        }

    def _normalize_rule(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        triggers = attrs.get("triggers") if isinstance(attrs.get("triggers"), list) else []
        actions = attrs.get("actions") if isinstance(attrs.get("actions"), list) else []
        return {
            "id": str(item.get("id")),
            "title": attrs.get("title") or "Untitled rule",
            "description": attrs.get("description"),
            "rule_group_id": str(attrs.get("rule_group_id") or ""),
            "trigger": attrs.get("trigger"),
            "active": bool(attrs.get("active", True)),
            "strict": bool(attrs.get("strict", True)),
            "triggers": [
                {
                    "type": self._as_dict(t).get("type"),
                    "value": self._as_dict(t).get("value"),
                }
                for t in triggers
                if isinstance(t, dict)
            ],
            "actions": [
                {
                    "type": self._as_dict(a).get("type"),
                    "value": self._as_dict(a).get("value"),
                }
                for a in actions
                if isinstance(a, dict)
            ],
        }

    async def list_rule_groups(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="RuleGroup",
                path="/api/v1/rule-groups",
                normalize=self._normalize_rule_group,
            )

        return await self._run_scoped(kb, _list)

    async def create_rule_group(
        self,
        kb: KBContext,
        *,
        title: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("Rule group title is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {"title": title, "active": True}
            if description:
                body["description"] = description
            payload = await self._request("POST", "/api/v1/rule-groups", json=body)
            return self._normalize_rule_group(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_rule_group(self, kb: KBContext, rule_group_id: str) -> None:
        rule_group_id = str(rule_group_id or "").strip()
        if not rule_group_id:
            raise ValueError("rule group id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/rule-groups/{rule_group_id}")

        await self._run_scoped(kb, _delete)

    async def list_rules(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="Rule",
                path="/api/v1/rules",
                normalize=self._normalize_rule,
            )

        return await self._run_scoped(kb, _list)

    async def create_rule(
        self,
        kb: KBContext,
        *,
        title: str,
        rule_group_id: str,
        trigger_type: str = "description_contains",
        trigger_value: str = "",
        action_type: str = "add_tag",
        action_value: str = "",
        trigger: str = "store-journal",
        description: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        rule_group_id = str(rule_group_id or "").strip()
        trigger_value = (trigger_value or "").strip()
        action_value = (action_value or "").strip()
        if not title:
            raise ValueError("Rule title is required")
        if not rule_group_id:
            raise ValueError("rule_group_id is required")
        if not trigger_value:
            raise ValueError("trigger_value is required")
        if not action_value:
            raise ValueError("action_value is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body = {
                "title": title,
                "rule_group_id": rule_group_id,
                "trigger": trigger or "store-journal",
                "active": True,
                "strict": True,
                "stop_processing": False,
                "triggers": [
                    {
                        "type": trigger_type or "description_contains",
                        "value": trigger_value,
                        "active": True,
                    }
                ],
                "actions": [
                    {
                        "type": action_type or "add_tag",
                        "value": action_value,
                        "active": True,
                    }
                ],
            }
            if description:
                body["description"] = description
            payload = await self._request("POST", "/api/v1/rules", json=body)
            return self._normalize_rule(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_rule(self, kb: KBContext, rule_id: str) -> None:
        rule_id = str(rule_id or "").strip()
        if not rule_id:
            raise ValueError("rule id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/rules/{rule_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_webhook(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        triggers = attrs.get("triggers") or attrs.get("trigger")
        responses = attrs.get("responses") or attrs.get("response")
        deliveries = attrs.get("deliveries") or attrs.get("delivery")
        if isinstance(triggers, str):
            triggers = [triggers]
        if isinstance(responses, str):
            responses = [responses]
        if isinstance(deliveries, str):
            deliveries = [deliveries]
        return {
            "id": str(item.get("id")),
            "title": attrs.get("title") or "Untitled webhook",
            "url": attrs.get("url"),
            "active": bool(attrs.get("active", False)),
            "triggers": triggers if isinstance(triggers, list) else [],
            "responses": responses if isinstance(responses, list) else [],
            "deliveries": deliveries if isinstance(deliveries, list) else [],
        }

    async def list_webhooks(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="Webhook",
                path="/api/v1/webhooks",
                normalize=self._normalize_webhook,
            )

        return await self._run_scoped(kb, _list)

    async def create_webhook(
        self,
        kb: KBContext,
        *,
        title: str,
        url: str,
        trigger: str = "STORE_TRANSACTION",
        response: str = "TRANSACTIONS",
        delivery: str = "JSON",
        active: bool = True,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        url = (url or "").strip()
        if not title:
            raise ValueError("Webhook title is required")
        if not url.startswith("https://"):
            raise ValueError("Webhook URL must start with https://")

        async def _create(_group_id: int) -> dict[str, Any]:
            body = {
                "title": title,
                "url": url,
                "active": active,
                "triggers": [trigger or "STORE_TRANSACTION"],
                "responses": [response or "TRANSACTIONS"],
                "deliveries": [delivery or "JSON"],
                # Older Firefly builds expect singular fields in required lists.
                "trigger": trigger or "STORE_TRANSACTION",
                "response": response or "TRANSACTIONS",
                "delivery": delivery or "JSON",
            }
            payload = await self._request("POST", "/api/v1/webhooks", json=body)
            return self._normalize_webhook(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _create)

    async def delete_webhook(self, kb: KBContext, webhook_id: str) -> None:
        webhook_id = str(webhook_id or "").strip()
        if not webhook_id:
            raise ValueError("webhook id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/webhooks/{webhook_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_object_group(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "title": attrs.get("title") or "Untitled group",
            "order": attrs.get("order"),
        }

    async def list_object_groups(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            return await self._list_scoped_resources(
                group_id,
                model="ObjectGroup",
                path="/api/v1/object-groups",
                normalize=self._normalize_object_group,
            )

        return await self._run_scoped(kb, _list)

    async def create_object_group(self, kb: KBContext, *, title: str) -> dict[str, Any]:
        """Firefly has no POST for groups; create via a bill that carries object_group_title."""
        title = (title or "").strip()
        if not title:
            raise ValueError("Group title is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            when = datetime.now(timezone.utc).isoformat()
            bill_payload = await self._request(
                "POST",
                "/api/v1/bills",
                json={
                    "name": f"{title} (group seed)",
                    "amount_min": "0.01",
                    "amount_max": "0.01",
                    "date": when,
                    "repeat_freq": "yearly",
                    "active": False,
                    "skip": 0,
                    "object_group_title": title,
                },
            )
            bill_data = self._as_dict(self._as_dict(bill_payload).get("data"))
            bill_id = bill_data.get("id")
            groups = await self._request("GET", "/api/v1/object-groups", params={"limit": 100})
            match = next(
                (
                    self._normalize_object_group(item)
                    for item in self._resource_rows(groups)
                    if self._as_dict(item.get("attributes")).get("title") == title
                ),
                None,
            )
            if bill_id:
                try:
                    await self._request("DELETE", f"/api/v1/bills/{bill_id}")
                except RuntimeError:
                    logger.warning("Could not remove seed bill %s for object group", bill_id)
            if match:
                return match
            raise RuntimeError(f"Created group seed for '{title}' but group was not listed back")

        return await self._run_scoped(kb, _create)

    async def update_object_group(
        self,
        kb: KBContext,
        group_id: str,
        *,
        title: str,
    ) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        title = (title or "").strip()
        if not group_id:
            raise ValueError("group id is required")
        if not title:
            raise ValueError("title is required")

        async def _update(_group_id: int) -> dict[str, Any]:
            payload = await self._request(
                "PUT",
                f"/api/v1/object-groups/{group_id}",
                json={"title": title},
            )
            return self._normalize_object_group(self._as_dict(self._as_dict(payload).get("data")))

        return await self._run_scoped(kb, _update)

    async def delete_object_group(self, kb: KBContext, group_id: str) -> None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/object-groups/{group_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_exchange_rate(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "date": attrs.get("date"),
            "rate": _as_float(attrs.get("rate")),
            "from": attrs.get("from") or attrs.get("from_currency_code"),
            "to": attrs.get("to") or attrs.get("to_currency_code"),
        }

    async def list_exchange_rates(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(_group_id: int) -> list[dict[str, Any]]:
            payload = await self._request("GET", "/api/v1/exchange-rates", params={"limit": 100})
            return [self._normalize_exchange_rate(item) for item in self._resource_rows(payload)]

        return await self._run_scoped(kb, _list)

    async def create_exchange_rate(
        self,
        kb: KBContext,
        *,
        date_value: str,
        from_code: str,
        to_code: str,
        rate: float,
    ) -> dict[str, Any]:
        from_code = (from_code or "").strip().upper()
        to_code = (to_code or "").strip().upper()
        if len(from_code) != 3 or len(to_code) != 3:
            raise ValueError("from/to must be 3-letter currency codes")
        if rate <= 0:
            raise ValueError("rate must be greater than zero")
        date_value = (date_value or _iso_today().isoformat()).strip()

        async def _create(_group_id: int) -> dict[str, Any]:
            rate_str = f"{rate:.8f}".rstrip("0").rstrip(".")
            body = {
                "date": date_value,
                "from": from_code,
                "to": to_code,
                "rate": rate_str,
                "rates": {to_code: rate_str},
            }
            payload = await self._request("POST", "/api/v1/exchange-rates", json=body)
            data = self._as_dict(self._as_dict(payload).get("data"))
            if data:
                return self._normalize_exchange_rate(data)
            listed = await self._request("GET", "/api/v1/exchange-rates", params={"limit": 100})
            rows = [self._normalize_exchange_rate(item) for item in self._resource_rows(listed)]
            match = next(
                (
                    row
                    for row in rows
                    if row.get("from") == from_code
                    and row.get("to") == to_code
                    and str(row.get("date") or "").startswith(date_value)
                ),
                None,
            )
            return match or {
                "id": "",
                "date": date_value,
                "rate": rate,
                "from": from_code,
                "to": to_code,
            }

        return await self._run_scoped(kb, _create)

    async def delete_exchange_rate(self, kb: KBContext, rate_id: str) -> None:
        rate_id = str(rate_id or "").strip()
        if not rate_id:
            raise ValueError("exchange rate id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/exchange-rates/{rate_id}")

        await self._run_scoped(kb, _delete)

    def _normalize_attachment(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = self._as_dict(item.get("attributes"))
        return {
            "id": str(item.get("id")),
            "filename": attrs.get("filename") or "file",
            "title": attrs.get("title") or attrs.get("filename"),
            "notes": attrs.get("notes"),
            "attachable_type": attrs.get("attachable_type"),
            "attachable_id": str(attrs.get("attachable_id") or ""),
            "size": attrs.get("size"),
            "mime": attrs.get("mime"),
            "download_url": attrs.get("download_url"),
        }

    async def list_attachments(self, kb: KBContext) -> list[dict[str, Any]]:
        async def _list(_group_id: int) -> list[dict[str, Any]]:
            payload = await self._request("GET", "/api/v1/attachments", params={"limit": 100})
            return [self._normalize_attachment(item) for item in self._resource_rows(payload)]

        return await self._run_scoped(kb, _list)

    async def create_attachment(
        self,
        kb: KBContext,
        *,
        filename: str,
        attachable_type: str,
        attachable_id: str,
        title: str | None = None,
        notes: str | None = None,
        file_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        filename = (filename or "").strip()
        attachable_type = (attachable_type or "").strip()
        attachable_id = str(attachable_id or "").strip()
        allowed = {"Account", "Budget", "Bill", "TransactionJournal", "PiggyBank", "Tag"}
        if not filename:
            raise ValueError("filename is required")
        if attachable_type not in allowed:
            raise ValueError(f"attachable_type must be one of: {', '.join(sorted(allowed))}")
        if not attachable_id:
            raise ValueError("attachable_id is required")

        async def _create(_group_id: int) -> dict[str, Any]:
            body: dict[str, Any] = {
                "filename": filename,
                "attachable_type": attachable_type,
                "attachable_id": attachable_id,
            }
            if title:
                body["title"] = title
            if notes:
                body["notes"] = notes
            payload = await self._request("POST", "/api/v1/attachments", json=body)
            attachment = self._normalize_attachment(self._as_dict(self._as_dict(payload).get("data")))
            if file_bytes is not None and attachment.get("id"):
                await self._request(
                    "POST",
                    f"/api/v1/attachments/{attachment['id']}/upload",
                    content=file_bytes,
                    headers={
                        **self._headers(),
                        "Content-Type": "application/octet-stream",
                    },
                )
            return attachment

        return await self._run_scoped(kb, _create)

    async def download_attachment(self, kb: KBContext, attachment_id: str) -> tuple[bytes, str]:
        attachment_id = str(attachment_id or "").strip()
        if not attachment_id:
            raise ValueError("attachment id is required")

        async def _download(_group_id: int) -> tuple[bytes, str]:
            meta_payload = await self._request("GET", f"/api/v1/attachments/{attachment_id}")
            meta = self._normalize_attachment(self._as_dict(self._as_dict(meta_payload).get("data")))
            async with self._client() as client:
                response = await client.request(
                    "GET",
                    f"/api/v1/attachments/{attachment_id}/download",
                )
                if response.is_error:
                    raise RuntimeError(
                        f"Firefly download failed ({response.status_code}): {response.text}"
                    )
                return response.content, str(meta.get("filename") or "attachment.bin")

        return await self._run_scoped(kb, _download)

    async def delete_attachment(self, kb: KBContext, attachment_id: str) -> None:
        attachment_id = str(attachment_id or "").strip()
        if not attachment_id:
            raise ValueError("attachment id is required")

        async def _delete(_group_id: int) -> None:
            await self._request("DELETE", f"/api/v1/attachments/{attachment_id}")

        await self._run_scoped(kb, _delete)

    async def search(
        self,
        kb: KBContext,
        *,
        query: str,
        kind: str = "transactions",
    ) -> dict[str, Any]:
        query = (query or "").strip()
        kind = (kind or "transactions").strip().lower()
        if not query:
            raise ValueError("query is required")
        if kind not in {"transactions", "accounts"}:
            raise ValueError("kind must be transactions or accounts")

        async def _search(group_id: int) -> dict[str, Any]:
            if kind == "accounts":
                allowed = await self._account_ids_for_group(group_id)
                payload = await self._request(
                    "GET",
                    "/api/v1/search/accounts",
                    params={"query": query, "field": "all", "limit": 50},
                )
                return {
                    "kind": "accounts",
                    "query": query,
                    "results": [
                        self._normalize_account(item)
                        for item in self._filter_api_data_by_ids(payload, allowed)
                    ],
                }
            payload = await self._request(
                "GET",
                "/api/v1/search/transactions",
                params={"query": query, "limit": 50},
            )
            rows: list[dict[str, Any]] = []
            for group in self._resource_rows(payload):
                if not self._transaction_group_belongs(group, group_id):
                    continue
                rows.extend(self._normalize_transaction_group(group))
            return {"kind": "transactions", "query": query, "results": rows}

        return await self._run_scoped(kb, _search)

    async def _activate_scope_locked(self, kb: KBContext) -> int:
        meta = kb_registry.get_metadata(kb.kb_id) or {}
        group_id = meta.get("firefly_group_id")
        if isinstance(group_id, str) and group_id.isdigit():
            group_id = int(group_id)
        title = self._group_title_for_kb(kb)

        if not isinstance(group_id, int) or group_id <= 0:
            runtime = self._load_runtime()
            if kb.kb_id == "default" and isinstance(runtime.get("groupId"), int):
                group_id = int(runtime["groupId"])
                kb_registry.set_firefly_group(kb.kb_id, group_id, title)
            else:
                created = await asyncio.to_thread(
                    self._run_php, self._php_create_group_script(title)
                )
                group_id = int(created["group_id"])
                kb_registry.set_firefly_group(kb.kb_id, group_id, title)

        if self._switched_group_id != group_id:
            await asyncio.to_thread(
                self._run_php, self._php_switch_group_script(group_id)
            )
            self._switched_group_id = group_id
        return group_id

    async def _run_scoped(self, kb: KBContext, callback):
        async with self._scope_lock:
            group_id = await self._activate_scope_locked(kb)
            prev = getattr(self, "_active_group_id", None)
            self._active_group_id = group_id
            try:
                return await callback(group_id)
            finally:
                self._active_group_id = prev

    async def status(self) -> dict[str, Any]:
        if not self.base_url:
            return {
                "ready": False,
                "exists": False,
                "status": "disabled",
                "detail": "Firefly base URL is not configured",
            }
        token = self._token()
        if not token:
            return {
                "ready": False,
                "exists": False,
                "status": "bootstrapping",
                "detail": "Firefly runtime has not finished minting its API token yet",
            }
        try:
            payload = await self._request("GET", "/api/v1/about")
            return {
                "ready": True,
                "exists": True,
                "status": "ready",
                "detail": "Embedded Firefly III is running",
                "about": payload.get("data") if isinstance(payload, dict) else None,
                "url": self.base_url,
            }
        except FireflyHTTPError as exc:
            return {
                "ready": False,
                "exists": False,
                "status": "auth_mismatch" if exc.status_code in (401, 403) else "error",
                "detail": str(exc),
                "url": self.base_url,
            }
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "ready": False,
                "exists": False,
                "status": "starting",
                "detail": str(exc),
                "url": self.base_url,
            }

    async def get_workspace(self, kb: KBContext) -> dict[str, Any]:
        state = await self.status()
        if not state["ready"]:
            return {
                "exists": False,
                "ready": False,
                "status": state["status"],
                "detail": state["detail"],
                "scope": "kb",
                "kb_id": kb.kb_id,
                "kb_name": kb.name,
                "firefly_url": self.base_url,
            }
        try:
            async def _load(group_id: int) -> dict[str, Any]:
                primary = await self._request("GET", "/api/v1/currencies/primary")
                group_data = await self._request("GET", "/api/v1/user-groups")
                groups = self._as_list(self._as_dict(group_data).get("data"))
                active_group = next(
                    (
                        g
                        for g in groups
                        if isinstance(g, dict) and str(g.get("id")) == str(group_id)
                    ),
                    groups[0] if groups else None,
                )
                group_attrs = self._as_dict(
                    active_group.get("attributes") if isinstance(active_group, dict) else None
                )
                currency_attrs = self._as_dict(
                    self._as_dict(self._as_dict(primary).get("data")).get("attributes")
                )
                meta = kb_registry.get_metadata(kb.kb_id) or {}
                administration_title = meta.get("firefly_group_title") or group_attrs.get(
                    "title"
                )
                return {
                    "exists": True,
                    "ready": True,
                    "status": "ready",
                    "scope": "kb",
                    "kb_id": kb.kb_id,
                    "kb_name": kb.name,
                    "firefly_group_id": group_id,
                    "currency": currency_attrs.get("code")
                    or group_attrs.get("primary_currency_code"),
                    "administration_title": administration_title,
                    "firefly_url": self.base_url,
                    "detail": (
                        f"Finance is scoped to the knowledge base \"{kb.name}\" "
                        f"via embedded Firefly administration #{group_id}."
                    ),
                }

            return await self._run_scoped(kb, _load)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "exists": False,
                "ready": False,
                "status": "error",
                "detail": str(exc),
                "scope": "kb",
                "kb_id": kb.kb_id,
                "kb_name": kb.name,
                "firefly_url": self.base_url,
            }

    async def set_primary_currency(self, kb: KBContext, currency_code: str) -> dict[str, Any]:
        code = (currency_code or "").strip().upper()
        if len(code) != 3:
            raise ValueError("Currency must be a 3-letter ISO code")

        async def _set(group_id: int) -> dict[str, Any]:
            await self._ensure_currency_enabled(code)
            await self._request("POST", f"/api/v1/currencies/{code}/primary")
            primary = await self._request("GET", "/api/v1/currencies/primary")
            currency_attrs = self._as_dict(
                self._as_dict(self._as_dict(primary).get("data")).get("attributes")
            )
            resolved = (currency_attrs.get("code") or code).upper()
            if resolved != code:
                raise RuntimeError(
                    f"Tried to set primary currency to {code}, but Firefly reports {resolved}."
                )
            meta = kb_registry.get_metadata(kb.kb_id) or {}
            return {
                "exists": True,
                "ready": True,
                "status": "ready",
                "scope": "kb",
                "kb_id": kb.kb_id,
                "kb_name": kb.name,
                "firefly_group_id": group_id,
                "currency": resolved,
                "administration_title": meta.get("firefly_group_title")
                or self._group_title_for_kb(kb),
                "firefly_url": self.base_url,
                "detail": (
                    f"Finance is scoped to the knowledge base \"{kb.name}\" "
                    f"via embedded Firefly administration #{group_id}."
                ),
            }

        return await self._run_scoped(kb, _set)

    async def _list_accounts_unlocked(self, group_id: int | None = None) -> list[dict[str, Any]]:
        allowed_ids: set[str] | None = None
        if isinstance(group_id, int) and group_id > 0:
            allowed_ids = await self._account_ids_for_group(group_id)

        all_accounts: list[dict[str, Any]] = []
        today = _iso_today().isoformat()
        for account_type in ("asset", "expense", "revenue", "liability"):
            params: dict[str, Any] = {"type": account_type, "limit": 100, "date": today}
            if isinstance(group_id, int) and group_id > 0:
                params["user_group_id"] = group_id
            payload = await self._request(
                "GET",
                "/api/v1/accounts",
                params=params,
            )
            all_accounts.extend(self._as_list(self._as_dict(payload).get("data")))
        rows = [
            self._normalize_account(item)
            for item in all_accounts
            if isinstance(item, dict)
            and (allowed_ids is None or str(item.get("id") or "") in allowed_ids)
        ]
        # Expense/revenue balances can lag in Firefly list payloads — enrich from txs.
        try:
            tx_params: dict[str, Any] = {"limit": 100}
            if isinstance(group_id, int) and group_id > 0:
                tx_params["user_group_id"] = group_id
            tx_payload = await self._request(
                "GET", "/api/v1/transactions", params=tx_params
            )
            spent_by_name: dict[str, float] = {}
            earned_by_name: dict[str, float] = {}
            for group in self._as_list(self._as_dict(tx_payload).get("data")):
                if not self._transaction_group_belongs(group, group_id):
                    continue
                for tx in self._normalize_transaction_group(group):
                    amount = abs(_as_float(tx.get("amount")))
                    if tx.get("type") == "withdrawal" and tx.get("counterparty_name"):
                        key = str(tx["counterparty_name"]).strip().lower()
                        spent_by_name[key] = spent_by_name.get(key, 0.0) + amount
                    elif tx.get("type") == "deposit" and tx.get("counterparty_name"):
                        key = str(tx["counterparty_name"]).strip().lower()
                        earned_by_name[key] = earned_by_name.get(key, 0.0) + amount
            for row in rows:
                name_key = (row.get("name") or "").strip().lower()
                atype = (row.get("account_type") or "").lower()
                if atype == "expense" and _as_float(row.get("balance")) == 0.0:
                    row["balance"] = spent_by_name.get(name_key, 0.0)
                elif atype == "revenue" and _as_float(row.get("balance")) == 0.0:
                    row["balance"] = earned_by_name.get(name_key, 0.0)
        except RuntimeError:
            logger.warning("Could not enrich expense/revenue balances from transactions")
        return rows

    async def list_accounts(self, kb: KBContext) -> list[dict[str, Any]]:
        return await self._run_scoped(
            kb, lambda group_id: self._list_accounts_unlocked(group_id)
        )

    @staticmethod
    def _transaction_group_belongs(item: dict[str, Any], group_id: int | None) -> bool:
        """True when a transaction group belongs to the KB's Firefly administration."""
        if not isinstance(group_id, int) or group_id <= 0:
            return True
        attrs = item.get("attributes") if isinstance(item, dict) else None
        if not isinstance(attrs, dict):
            return False
        ug = attrs.get("user_group")
        if ug is None:
            ug = attrs.get("user_group_id")
        try:
            return int(ug) == int(group_id)
        except (TypeError, ValueError):
            return str(ug) == str(group_id)

    def _normalize_transaction_group(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        attrs = item.get("attributes", {}) if isinstance(item, dict) else {}
        transactions = attrs.get("transactions") or []
        rows: list[dict[str, Any]] = []
        for idx, tx in enumerate(transactions if isinstance(transactions, list) else []):
            tx = tx if isinstance(tx, dict) else {}
            rows.append(
                {
                    "id": f"{item.get('id')}:{idx}",
                    "group_id": str(item.get("id")),
                    "user_group": attrs.get("user_group") or attrs.get("user_group_id"),
                    "date": tx.get("date") or attrs.get("date"),
                    "description": tx.get("description") or attrs.get("description") or "",
                    "amount": _as_float(tx.get("amount")),
                    "type": tx.get("type") or attrs.get("type"),
                    "account_id": tx.get("source_id")
                    or tx.get("destination_id")
                    or "",
                    "account_name": tx.get("source_name") or tx.get("destination_name") or "",
                    "counterparty_name": tx.get("destination_name")
                    if tx.get("type") == "withdrawal"
                    else tx.get("source_name"),
                    "category": tx.get("category_name"),
                    "currency_code": tx.get("currency_code"),
                    "journal_id": str(tx.get("transaction_journal_id") or item.get("id") or ""),
                }
            )
        return rows

    async def list_recent_transactions(
        self,
        kb: KBContext,
        *,
        account_id: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        async def _list(group_id: int) -> list[dict[str, Any]]:
            params: dict[str, Any] = {"limit": limit, "user_group_id": group_id}
            if account_id:
                # Only show txs for accounts that belong to this KB's group
                allowed = await self._account_ids_for_group(group_id)
                if allowed and str(account_id) not in allowed:
                    return []
                payload = await self._request(
                    "GET",
                    f"/api/v1/accounts/{account_id}/transactions",
                    params=params,
                )
            else:
                payload = await self._request(
                    "GET", "/api/v1/transactions", params=params
                )
            groups = self._as_list(self._as_dict(payload).get("data"))
            rows: list[dict[str, Any]] = []
            for group in groups:
                if not self._transaction_group_belongs(group, group_id):
                    continue
                rows.extend(self._normalize_transaction_group(group))
            rows.sort(key=lambda item: item.get("date") or "", reverse=True)
            return rows[:limit]

        return await self._run_scoped(kb, _list)

    async def summary(self, kb: KBContext, *, days: int = 30) -> dict[str, Any]:
        async def _summary(group_id: int) -> dict[str, Any]:
            accounts = await self._list_accounts_unlocked(group_id)
            end = _iso_today()
            start = end - timedelta(days=max(days, 1) - 1)
            payload = await self._request(
                "GET",
                "/api/v1/transactions",
                params={"limit": 100, "user_group_id": group_id},
            )
            groups = self._as_list(self._as_dict(payload).get("data"))
            txs: list[dict[str, Any]] = []
            for group in groups:
                if not self._transaction_group_belongs(group, group_id):
                    continue
                txs.extend(self._normalize_transaction_group(group))
            txs.sort(key=lambda item: item.get("date") or "", reverse=True)
            expense_total = 0.0
            income_total = 0.0
            transfers_total = 0.0
            for tx in txs:
                tx_date_raw = tx.get("date")
                if not tx_date_raw:
                    continue
                tx_day = datetime.fromisoformat(
                    str(tx_date_raw).replace("Z", "+00:00")
                ).date()
                if not (start <= tx_day <= end):
                    continue
                amount = _as_float(tx.get("amount"))
                tx_type = tx.get("type")
                if tx_type == "withdrawal":
                    expense_total += abs(amount)
                elif tx_type == "deposit":
                    income_total += abs(amount)
                elif tx_type == "transfer":
                    transfers_total += abs(amount)
            asset_balance = sum(
                _as_float(a.get("balance"))
                for a in accounts
                if a.get("account_type") in ("asset", "liability", "liabilities")
            )
            chart = await self._request(
                "GET",
                "/api/v1/chart/balance/balance",
                params={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "period": "1D",
                    "user_group_id": group_id,
                },
            )
            return {
                "days": days,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "asset_balance": asset_balance,
                "income_total": income_total,
                "expense_total": expense_total,
                "transfer_total": transfers_total,
                "net_flow": income_total - expense_total,
                "chart": self._as_dict(chart).get("data"),
                "accounts": accounts,
                "recent_transactions": txs[:12],
                "kb_id": kb.kb_id,
                "kb_name": kb.name,
            }

        return await self._run_scoped(kb, _summary)

    async def prepare_open(self, kb: KBContext) -> dict[str, Any]:
        group_id = await self.ensure_kb_scope(kb)
        meta = kb_registry.get_metadata(kb.kb_id) or {}
        return {
            "url": self.base_url,
            "kb_id": kb.kb_id,
            "kb_name": kb.name,
            "firefly_group_id": group_id,
            "administration_title": meta.get("firefly_group_title")
            or self._group_title_for_kb(kb),
        }

    def _format_note_passages(self, note_docs: list[dict[str, Any]]) -> str:
        passages: list[str] = []
        for idx, doc in enumerate(note_docs[:6], start=1):
            node = doc.get("original_obj", {}) if isinstance(doc, dict) else {}
            title = node.get("name") or doc.get("note_id") or f"Note {idx}"
            text = (
                node.get("summary")
                or node.get("description")
                or doc.get("text")
                or ""
            ).strip()
            if not text:
                continue
            passages.append(f"[{title}] {text[:1200]}")
        return "\n".join(passages)

    async def answer_finance_question(
        self,
        query: str,
        kb: KBContext,
        *,
        note_docs: list[dict[str, Any]] | None = None,
        rewritten_query: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        load_before = load_snapshot()
        workspace = await self.get_workspace(kb)
        if not workspace.get("ready"):
            detail = workspace.get("detail") or "Firefly III is not ready yet."
            return {
                "query": query,
                "rewritten_query": rewritten_query or query,
                "answer": f"I can't answer finance questions yet because {detail}",
                "context": note_docs or [],
                "information_needs": [query],
                "discovered_entities": {},
                "thinking": None,
            }
        summary = await self.summary(kb, days=30)
        note_passages = self._format_note_passages(note_docs or [])
        finance_payload = {"workspace": workspace, "summary": summary}
        system = (
            "You answer finance questions for Orb using two sources:\n"
            "1. Firefly III ledger data (balances, transactions, reports) — treat as authoritative for numbers.\n"
            "2. Relevant knowledge-base notes — use for budgets, plans, context, and explanations.\n"
            "Blend both when helpful. Be concise and explicit about date ranges. "
            "Do not invent transactions or balances. If notes conflict with ledger data, prefer the ledger."
        )
        user_parts = [f"User question:\n{query}"]
        if rewritten_query and rewritten_query != query:
            user_parts.append(f"Rewritten retrieval query:\n{rewritten_query}")
        user_parts.append(
            f"Firefly finance context JSON:\n{json.dumps(finance_payload, indent=2)}"
        )
        if note_passages:
            user_parts.append(f"Relevant note passages from KB \"{kb.name}\":\n{note_passages}")
        else:
            user_parts.append(
                f"No relevant note passages were retrieved from KB \"{kb.name}\"."
            )
        # Use this KB's LLM (a pinned per-KB model, or the system default), and
        # run it in a worker thread: the call is synchronous and — now that a KB
        # can pin a different GGUF — may swap a multi-GB model here. Inline, that
        # would block the event loop serving /chat/status polls.
        answer = await asyncio.to_thread(
            kb.llm.generate_text, system, "\n\n".join(user_parts)
        )
        log_stage_timing(
            logger,
            "finance_chat",
            time.perf_counter() - started,
            load_before,
            kb=kb.name,
            docs=len(note_docs or []),
        )
        context: list[dict[str, Any]] = list(note_docs or [])
        context.append({"source": "finance", "summary": summary, "kb_id": kb.kb_id})
        return {
            "query": query,
            "rewritten_query": rewritten_query or query,
            "answer": answer,
            "context": context,
            "information_needs": [query],
            "discovered_entities": {},
            "thinking": None,
        }

    @staticmethod
    def looks_like_finance_query(query: str) -> bool:
        text = (query or "").lower()
        keywords = (
            "balance",
            "balances",
            "transaction",
            "transactions",
            "spending",
            "spent",
            "income",
            "expense",
            "expenses",
            "budget",
            "cash",
            "account",
            "accounts",
            "finance",
            "financial",
            "report",
            "net worth",
            "savings",
        )
        return any(word in text for word in keywords)


firefly_service = FireflyService()
