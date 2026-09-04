/**
 * Cloud API keys, encrypted at rest with the OS keychain.
 *
 * End users cannot edit `.env`, so keys are entered in the app and stored here
 * via Electron `safeStorage` (Keychain on macOS, DPAPI on Windows, libsecret on
 * Linux). The ciphertext lives in DATA_DIR/credentials.enc; the plaintext only
 * ever exists in memory and in the backend process it is pushed to.
 *
 * DATA_DIR is often a synced folder (OneDrive/NAS), so writing a plaintext
 * secret there would copy it off the machine. If the OS cannot encrypt, we
 * refuse to store rather than silently degrade.
 */
const fs = require("fs");
const path = require("path");
const http = require("http");

const FILE_NAME = "credentials.enc";
const KNOWN_PROVIDERS = ["openai", "gemini", "anthropic", "huggingface"];

/**
 * OpenAI-compatible servers are identified by their URL rather than a name the
 * user invents, so two endpoints can never share a key by accident. Must match
 * normalize_base_url() in backend/app/services/credentials.py.
 */
const ENDPOINT_PREFIX = "endpoint:";

function normalizeBaseUrl(raw) {
  const text = String(raw || "").trim();
  if (!text) throw new Error("Endpoint URL must not be empty");
  let url;
  try {
    url = new URL(text);
  } catch (_) {
    throw new Error(`Endpoint URL must start with http:// or https:// (got '${text}')`);
  }
  const scheme = url.protocol.replace(":", "").toLowerCase();
  if (scheme !== "http" && scheme !== "https") {
    throw new Error(`Endpoint URL must start with http:// or https:// (got '${text}')`);
  }
  if (!url.hostname) throw new Error(`Endpoint URL has no host: '${text}'`);
  const host = url.port
    ? `${url.hostname.toLowerCase()}:${url.port}`
    : url.hostname.toLowerCase();
  const path = url.pathname.replace(/\/+$/, "");
  return `${scheme}://${host}${path}`;
}

function endpointCredentialId(baseUrl) {
  return `${ENDPOINT_PREFIX}${normalizeBaseUrl(baseUrl)}`;
}

function isEndpointId(name) {
  return String(name || "").startsWith(ENDPOINT_PREFIX);
}

function credentialsFile(dataDir) {
  return path.join(dataDir, FILE_NAME);
}

function normalizeProvider(provider) {
  const raw = String(provider || "").trim();
  if (isEndpointId(raw)) {
    // Only the URL is canonicalised; a path may be case-sensitive.
    return endpointCredentialId(raw.slice(ENDPOINT_PREFIX.length));
  }
  const name = raw.toLowerCase();
  if (name === "google") return "gemini";
  if (name === "hf") return "huggingface";
  return name;
}

function isKnownProvider(provider) {
  const raw = String(provider || "").trim();
  if (isEndpointId(raw)) {
    try {
      normalizeBaseUrl(raw.slice(ENDPOINT_PREFIX.length));
      return true;
    } catch (_) {
      return false;
    }
  }
  return KNOWN_PROVIDERS.includes(normalizeProvider(raw));
}

/** Electron's safeStorage, or a stand-in supplied by tests. */
function getSafeStorage(injected) {
  if (injected) return injected;
  // Required lazily so this module can be unit-tested outside Electron.
  return require("electron").safeStorage;
}

/**
 * Whether this machine can genuinely encrypt secrets, and with what.
 *
 * Windows (DPAPI) and macOS (Keychain) are part of the OS and are always
 * available once the app is ready. Only Linux can lack a backend — and there it
 * can also report `basic_text`, which derives the key from a hardcoded constant.
 * That is obfuscation, not encryption, so we treat it as unavailable rather than
 * telling the user their key is protected when it is not.
 */
function encryptionStatus(injected, platform = process.platform) {
  const ss = getSafeStorage(injected);
  let available = false;
  try {
    available = Boolean(ss?.isEncryptionAvailable?.());
  } catch (_) {
    available = false;
  }

  if (platform !== "linux") {
    return {
      available,
      backend: platform === "darwin" ? "keychain" : "dpapi",
      reason: available ? "" : "The operating system reported no encryption backend.",
    };
  }

  let backend = "unknown";
  try {
    backend = ss?.getSelectedStorageBackend?.() || "unknown";
  } catch (_) {
    backend = "unknown";
  }
  const real = available && backend !== "basic_text" && backend !== "unknown";
  return {
    available: real,
    backend,
    reason: real
      ? ""
      : backend === "basic_text"
        ? "No system keyring was found, so Electron would encrypt with a hardcoded key — that is not real protection."
        : "No system keyring (gnome-keyring, kwallet) is available.",
  };
}

function encryptionAvailable(injected, platform = process.platform) {
  return encryptionStatus(injected, platform).available;
}

/** Read and decrypt all stored keys. Returns {} when absent or unreadable. */
function loadCredentials(dataDir, injected) {
  const file = credentialsFile(dataDir);
  if (!fs.existsSync(file)) return {};
  if (!encryptionAvailable(injected)) return {};
  try {
    const blob = fs.readFileSync(file);
    const json = getSafeStorage(injected).decryptString(blob);
    const parsed = JSON.parse(json);
    if (!parsed || typeof parsed !== "object") return {};
    const out = {};
    for (const [provider, key] of Object.entries(parsed)) {
      if (isKnownProvider(provider) && typeof key === "string" && key) {
        out[normalizeProvider(provider)] = key;
      }
    }
    return out;
  } catch (err) {
    // A keychain the OS will not unlock, or a file from another machine.
    console.error(`Could not read ${FILE_NAME}: ${err.message}`);
    return {};
  }
}

function writeCredentials(dataDir, secrets, injected) {
  const status = encryptionStatus(injected);
  if (!status.available) {
    throw new Error(
      `${status.reason} Orb will not write API keys to disk unencrypted, so keys ` +
        "are kept for this session only.",
    );
  }
  const file = credentialsFile(dataDir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const blob = getSafeStorage(injected).encryptString(JSON.stringify(secrets));
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, blob, { mode: 0o600 });
  fs.renameSync(tmp, file);
  try {
    fs.chmodSync(file, 0o600);
  } catch (_) {
    /* best effort on non-POSIX */
  }
}

function saveCredential(dataDir, provider, apiKey, injected) {
  const name = normalizeProvider(provider);
  if (!isKnownProvider(name)) throw new Error(`Unknown provider '${provider}'`);
  const key = String(apiKey || "").trim();
  if (!key) throw new Error("API key must not be empty");
  const secrets = loadCredentials(dataDir, injected);
  secrets[name] = key;
  writeCredentials(dataDir, secrets, injected);
  return name;
}

function deleteCredential(dataDir, provider, injected) {
  const name = normalizeProvider(provider);
  const secrets = loadCredentials(dataDir, injected);
  const existed = Object.prototype.hasOwnProperty.call(secrets, name);
  delete secrets[name];
  if (existed) writeCredentials(dataDir, secrets, injected);
  return existed;
}

/** Which providers and endpoints have a stored key — never the keys themselves. */
function listCredentials(dataDir, injected) {
  const secrets = loadCredentials(dataDir, injected);
  const status = encryptionStatus(injected);
  return {
    encryptionAvailable: status.available,
    encryptionBackend: status.backend,
    encryptionReason: status.reason,
    providers: KNOWN_PROVIDERS.map((name) => ({
      provider: name,
      configured: Boolean(secrets[name]),
    })),
    endpoints: Object.keys(secrets)
      .filter(isEndpointId)
      .map((id) => id.slice(ENDPOINT_PREFIX.length))
      .sort(),
  };
}

/**
 * Move an existing store out of a previous location.
 *
 * The first implementation kept `credentials.enc` in DATA_DIR, which is commonly
 * a synced folder (OneDrive, NAS) — uploading ciphertext that only this machine
 * can read is pointless, so the store now lives in the app's local userData.
 */
function migrateLegacyStore(fromDir, toDir) {
  try {
    if (!fromDir || !toDir || path.resolve(fromDir) === path.resolve(toDir)) return false;
    const from = credentialsFile(fromDir);
    const to = credentialsFile(toDir);
    if (!fs.existsSync(from) || fs.existsSync(to)) return false;
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    fs.chmodSync(to, 0o600);
    fs.unlinkSync(from);
    return true;
  } catch (err) {
    console.error(`Could not migrate credentials store: ${err.message}`);
    return false;
  }
}

function request(apiBase, method, providerPath, body) {
  return new Promise((resolve, reject) => {
    let url;
    try {
      url = new URL(`${apiBase}/credentials/${providerPath}`);
    } catch (err) {
      reject(err);
      return;
    }
    const payload = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method,
        timeout: 10000,
        headers: payload
          ? { "Content-Type": "application/json", "Content-Length": payload.length }
          : {},
      },
      (res) => {
        res.resume();
        res.on("end", () =>
          res.statusCode && res.statusCode < 400
            ? resolve()
            : reject(new Error(`${method} credentials returned ${res.statusCode}`)),
        );
      },
    );
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Timed out pushing credentials to the API"));
    });
    if (payload) req.write(payload);
    req.end();
  });
}

/** Push one key into the running API so it takes effect without a restart. */
async function pushCredential(apiBase, provider, apiKey) {
  const name = normalizeProvider(provider);
  if (isEndpointId(name)) {
    await request(apiBase, "PUT", "endpoint", {
      base_url: name.slice(ENDPOINT_PREFIX.length),
      api_key: apiKey,
    });
    return;
  }
  await request(apiBase, "PUT", name, { api_key: apiKey, source: "keychain" });
}

async function clearCredentialOnBackend(apiBase, provider) {
  const name = normalizeProvider(provider);
  if (isEndpointId(name)) {
    const query = encodeURIComponent(name.slice(ENDPOINT_PREFIX.length));
    await request(apiBase, "DELETE", `endpoint?base_url=${query}`, null);
    return;
  }
  await request(apiBase, "DELETE", name, null);
}

/** Push every stored key at boot. Failures are logged, never fatal. */
async function pushAllCredentials(dataDir, apiBase, injected) {
  const secrets = loadCredentials(dataDir, injected);
  const names = Object.keys(secrets);
  for (const name of names) {
    try {
      await pushCredential(apiBase, name, secrets[name]);
    } catch (err) {
      console.error(`Could not push ${name} credential: ${err.message}`);
    }
  }
  return names.length;
}

module.exports = {
  KNOWN_PROVIDERS,
  ENDPOINT_PREFIX,
  credentialsFile,
  normalizeBaseUrl,
  endpointCredentialId,
  isEndpointId,
  normalizeProvider,
  isKnownProvider,
  encryptionAvailable,
  encryptionStatus,
  migrateLegacyStore,
  loadCredentials,
  saveCredential,
  deleteCredential,
  listCredentials,
  pushCredential,
  clearCredentialOnBackend,
  pushAllCredentials,
};
