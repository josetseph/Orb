const test = require("node:test");
const assert = require("node:assert");

// shouldRetryLoad is the only branch in the retry path worth pinning.
const { shouldRetryLoad } = (() => {
  const src = require("fs").readFileSync(require("path").join(__dirname, "main.js"), "utf8");
  const body = src.slice(src.indexOf("function shouldRetryLoad"));
  const fn = body.slice(0, body.indexOf("\n}") + 2);
  return eval(`(() => { ${fn} return { shouldRetryLoad }; })()`);
})();

test("retries a failed main-frame load", () => {
  assert.equal(shouldRetryLoad(-102, true), true); // CONNECTION_REFUSED
});

test("ignores an aborted navigation", () => {
  assert.equal(shouldRetryLoad(-3, true), false);
});

test("ignores subframes", () => {
  assert.equal(shouldRetryLoad(-102, false), false);
});
