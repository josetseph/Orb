"""The credential store must round-trip through the OS keychain (faked here)."""

import json
import sys
import types


def _fake_keyring(store: dict):
    mod = types.ModuleType("keyring")
    mod.errors = types.SimpleNamespace(PasswordDeleteError=KeyError)
    mod.get_password = lambda svc, name: store.get((svc, name))
    mod.set_password = lambda svc, name, value: store.__setitem__((svc, name), value)
    mod.delete_password = lambda svc, name: store.pop((svc, name))
    return mod


def test_keys_persist_and_reload(monkeypatch):
    store: dict = {}
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(store))
    from app.services.credentials import CredentialStore

    first = CredentialStore()
    first.set("openai", "sk-test")
    first.set("endpoint:http://localhost:1234", "not-needed")
    assert json.loads(store[("Orb", "__index__")]) == ["endpoint:http://localhost:1234", "openai"]

    second = CredentialStore()  # a fresh process
    assert second.get("openai") == "sk-test"
    assert second.status()["openai"]["source"] == "keychain"
    assert second.endpoints() == ["http://localhost:1234"]

    second.clear("openai")
    assert ("Orb", "openai") not in store
    assert json.loads(store[("Orb", "__index__")]) == ["endpoint:http://localhost:1234"]
    assert CredentialStore().get("openai") is None


def test_no_keychain_is_session_only(monkeypatch):
    broken = types.ModuleType("keyring")
    broken.get_password = lambda *_: (_ for _ in ()).throw(RuntimeError("no backend"))
    monkeypatch.setitem(sys.modules, "keyring", broken)
    from app.services.credentials import CredentialStore

    s = CredentialStore()
    s.set("openai", "sk-live")  # must not raise
    assert s.get("openai") == "sk-live"
