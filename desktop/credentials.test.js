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
