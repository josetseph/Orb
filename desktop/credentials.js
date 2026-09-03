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

function credentialsFile(dataDir) {
  return path.join(dataDir, FILE_NAME);
}

function normalizeProvider(provider) {
  const name = String(provider || "").trim().toLowerCase();
  if (name === "google") return "gemini";
  if (name === "hf") return "huggingface";
  return name;
}

function isKnownProvider(provider) {
  return KNOWN_PROVIDERS.includes(normalizeProvider(provider));
}

/** Electron's safeStorage, or a stand-in supplied by tests. */
function getSafeStorage(injected) {
  if (injected) return injected;
  // Required lazily so this module can be unit-tested outside Electron.
  return require("electron").safeStorage;
}

function encryptionAvailable(injected) {
  try {
    return Boolean(getSafeStorage(injected)?.isEncryptionAvailable?.());
  } catch (_) {
    return false;
  }
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
  if (!encryptionAvailable(injected)) {
    throw new Error(
      "This system cannot encrypt secrets (no OS keychain available), so Orb " +
        "will not store API keys on disk.",
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

/** Which providers have a stored key — never the keys themselves. */
function listCredentials(dataDir, injected) {
  const secrets = loadCredentials(dataDir, injected);
  return {
    encryptionAvailable: encryptionAvailable(injected),
    providers: KNOWN_PROVIDERS.map((name) => ({
      provider: name,
      configured: Boolean(secrets[name]),
    })),
  };
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
  await request(apiBase, "PUT", normalizeProvider(provider), {
    api_key: apiKey,
    source: "keychain",
  });
}

async function clearCredentialOnBackend(apiBase, provider) {
  await request(apiBase, "DELETE", normalizeProvider(provider), null);
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
  credentialsFile,
  normalizeProvider,
  isKnownProvider,
  encryptionAvailable,
  loadCredentials,
  saveCredential,
  deleteCredential,
  listCredentials,
  pushCredential,
  clearCredentialOnBackend,
  pushAllCredentials,
};
