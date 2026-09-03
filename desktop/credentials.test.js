/**
 * Tests for the encrypted credential store (node --test, no dependencies).
 *
 * safeStorage is injected so these run outside Electron. The fake base64-encodes,
 * which is enough to assert the plaintext key never lands on disk.
 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const store = require("./credentials");

/** Stand-in for Electron safeStorage. */
function fakeSafeStorage({ available = true } = {}) {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (text) => Buffer.from(`enc:${text}`).toString("base64"),
    decryptString: (buf) =>
      Buffer.from(buf.toString(), "base64").toString("utf8").replace(/^enc:/, ""),
  };
}

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "orb-cred-"));
}

test("saves and reads back a key", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, "openai", "sk-test-123", ss);
  assert.strictEqual(store.loadCredentials(dir, ss).openai, "sk-test-123");
});

test("the plaintext key never appears in the file on disk", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, "openai", "sk-SUPER-SECRET", ss);
  const raw = fs.readFileSync(store.credentialsFile(dir), "utf8");
  assert.ok(!raw.includes("sk-SUPER-SECRET"), "secret was written in plaintext");
});

test("the credentials file is owner-only", () => {
  const dir = tmpDir();
  store.saveCredential(dir, "openai", "sk-1", fakeSafeStorage());
  const mode = fs.statSync(store.credentialsFile(dir)).mode & 0o777;
  assert.strictEqual(mode, 0o600);
});

test("refuses to store anything when the OS cannot encrypt", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage({ available: false });
  assert.throws(
    () => store.saveCredential(dir, "openai", "sk-1", ss),
    /cannot encrypt secrets/,
  );
  assert.ok(!fs.existsSync(store.credentialsFile(dir)), "wrote a file anyway");
});

test("rejects unknown providers and empty keys", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  assert.throws(() => store.saveCredential(dir, "notaprovider", "k", ss), /Unknown provider/);
  assert.throws(() => store.saveCredential(dir, "openai", "   ", ss), /must not be empty/);
});

test("normalises provider aliases", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, "Google", "g-1", ss);
  assert.strictEqual(store.loadCredentials(dir, ss).gemini, "g-1");
  assert.strictEqual(store.normalizeProvider("HF"), "huggingface");
});

test("keeps other providers when one is saved or deleted", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, "openai", "sk-1", ss);
  store.saveCredential(dir, "gemini", "g-1", ss);
  assert.strictEqual(store.deleteCredential(dir, "openai", ss), true);
  const left = store.loadCredentials(dir, ss);
  assert.deepStrictEqual(Object.keys(left), ["gemini"]);
  assert.strictEqual(store.deleteCredential(dir, "openai", ss), false);
});

test("listCredentials reports configuration without key material", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, "anthropic", "sk-ant-SECRET", ss);
  const listed = store.listCredentials(dir, ss);
  assert.strictEqual(listed.encryptionAvailable, true);
  const anthropic = listed.providers.find((p) => p.provider === "anthropic");
  assert.strictEqual(anthropic.configured, true);
  assert.ok(!JSON.stringify(listed).includes("sk-ant-SECRET"));
  assert.strictEqual(
    listed.providers.find((p) => p.provider === "openai").configured,
    false,
  );
});

test("missing or corrupt files degrade to empty rather than throwing", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  assert.deepStrictEqual(store.loadCredentials(dir, ss), {});
  fs.writeFileSync(store.credentialsFile(dir), "not-valid-base64-json");
  assert.deepStrictEqual(store.loadCredentials(dir, ss), {});
});

test("unknown or malformed entries in the file are ignored", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  fs.writeFileSync(
    store.credentialsFile(dir),
    ss.encryptString(JSON.stringify({ openai: "sk-1", bogus: "x", gemini: 42 })),
  );
  assert.deepStrictEqual(store.loadCredentials(dir, ss), { openai: "sk-1" });
});

test("reads nothing when encryption became unavailable", () => {
  const dir = tmpDir();
  store.saveCredential(dir, "openai", "sk-1", fakeSafeStorage());
  const locked = fakeSafeStorage({ available: false });
  assert.deepStrictEqual(store.loadCredentials(dir, locked), {});
});

// ── OpenAI-compatible endpoints ────────────────────────────────────────────
// URL normalisation must match normalize_base_url() in
// backend/app/services/credentials.py, or a key saved here would be looked up
// under a different id by the backend.

test("normalises endpoint URLs the same way the backend does", () => {
  assert.strictEqual(
    store.normalizeBaseUrl("HTTPS://Api.Example.COM/v1/"),
    "https://api.example.com/v1",
  );
  assert.strictEqual(store.normalizeBaseUrl("  https://api.test/v1  "), "https://api.test/v1");
  assert.strictEqual(store.normalizeBaseUrl("https://api.test/v1?x=1#f"), "https://api.test/v1");
  assert.strictEqual(
    store.normalizeBaseUrl("http://127.0.0.1:8080/v1"),
    "http://127.0.0.1:8080/v1",
  );
});

test("rejects endpoint URLs that are not http(s)", () => {
  for (const bad of ["ftp://x/v1", "file:///etc/passwd", "notaurl", "", "   "]) {
    assert.throws(() => store.normalizeBaseUrl(bad));
  }
});

test("stores a key per endpoint URL", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, store.endpointCredentialId("https://openrouter.ai/api/v1"), "or-key", ss);
  store.saveCredential(dir, store.endpointCredentialId("https://api.groq.com/openai/v1"), "groq-key", ss);
  const secrets = store.loadCredentials(dir, ss);
  assert.strictEqual(secrets["endpoint:https://openrouter.ai/api/v1"], "or-key");
  assert.strictEqual(secrets["endpoint:https://api.groq.com/openai/v1"], "groq-key");
});

test("equivalent endpoint URLs resolve to one stored key", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, store.endpointCredentialId("https://API.test/v1/"), "k-1", ss);
  const id = store.endpointCredentialId("https://api.test/v1");
  assert.strictEqual(store.loadCredentials(dir, ss)[id], "k-1");
});

test("lists endpoints without exposing their keys", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  store.saveCredential(dir, store.endpointCredentialId("https://openrouter.ai/api/v1"), "SECRET-OR", ss);
  store.saveCredential(dir, "openai", "SECRET-OAI", ss);
  const listed = store.listCredentials(dir, ss);
  assert.deepStrictEqual(listed.endpoints, ["https://openrouter.ai/api/v1"]);
  const json = JSON.stringify(listed);
  assert.ok(!json.includes("SECRET-OR") && !json.includes("SECRET-OAI"));
  assert.strictEqual(listed.providers.find((p) => p.provider === "openai").configured, true);
});

test("a malformed endpoint id is not storable, and says why", () => {
  const dir = tmpDir();
  assert.throws(
    () => store.saveCredential(dir, "endpoint:notaurl", "k", fakeSafeStorage()),
    /must start with http/,
  );
  assert.strictEqual(store.isKnownProvider("endpoint:notaurl"), false);
  assert.strictEqual(store.isKnownProvider("endpoint:https://ok.test/v1"), true);
});

test("endpoint entries survive a reload and delete independently", () => {
  const dir = tmpDir();
  const ss = fakeSafeStorage();
  const a = store.endpointCredentialId("https://a.test/v1");
  const b = store.endpointCredentialId("https://b.test/v1");
  store.saveCredential(dir, a, "ka", ss);
  store.saveCredential(dir, b, "kb", ss);
  assert.strictEqual(store.deleteCredential(dir, a, ss), true);
  assert.deepStrictEqual(Object.keys(store.loadCredentials(dir, ss)), [b]);
});
