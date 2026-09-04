/**
 * Content stamps that prove a packaged tree was built from the current sources.
 *
 * prepare-dist runs its stages in sequence and stops at the first failure, so a
 * partial run leaves a mix of fresh and month-old trees that every existence
 * check happily accepts — which is how a build shipped a UI three weeks older
 * than its backend. Each stage writes a stamp of the sources it consumed;
 * `check-resources` recomputes and refuses to package on a mismatch.
 *
 * Hashing content rather than comparing mtimes means a `git checkout` (which
 * rewrites mtimes) does not produce a spurious "stale" verdict.
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const STAMP_FILE = ".orb-source-stamp.json";

// Build inputs only: caches and build outputs must not affect the stamp.
const SKIP_DIRS = new Set([
  "node_modules",
  ".next",
  "__pycache__",
  ".venv",
  "venv",
  ".git",
  ".pytest_cache",
  ".ruff_cache",
  ".mypy_cache",
]);

function shouldSkip(name) {
  return SKIP_DIRS.has(name) || name.startsWith(".");
}

/** Hash every file under `roots`, keyed by path relative to each root. */
function hashSources(roots) {
  const hash = crypto.createHash("sha256");
  for (const { label, dir } of roots) {
    if (!fs.existsSync(dir)) {
      hash.update(`${label}:missing\n`);
      continue;
    }
    // A root may be a single file (e.g. next.config.ts) as well as a directory.
    if (fs.statSync(dir).isFile()) {
      hash.update(`${label}\0`);
      hash.update(fs.readFileSync(dir));
      hash.update("\0");
      continue;
    }
    const files = [];
    const walk = (current, rel) => {
      for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((a, b) =>
        a.name < b.name ? -1 : 1,
      )) {
        if (shouldSkip(entry.name)) continue;
        const abs = path.join(current, entry.name);
        const relPath = rel ? `${rel}/${entry.name}` : entry.name;
        if (entry.isDirectory()) walk(abs, relPath);
        else if (entry.isFile()) files.push([relPath, abs]);
      }
    };
    walk(dir, "");
    for (const [relPath, abs] of files) {
      hash.update(`${label}/${relPath}\0`);
      hash.update(fs.readFileSync(abs));
      hash.update("\0");
    }
  }
  return hash.digest("hex");
}

function stampPath(outDir) {
  return path.join(outDir, STAMP_FILE);
}

function writeStamp(outDir, roots) {
  const stamp = { hash: hashSources(roots), builtAt: new Date().toISOString() };
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(stampPath(outDir), JSON.stringify(stamp, null, 2), "utf8");
  return stamp;
}

function readStamp(outDir) {
  try {
    return JSON.parse(fs.readFileSync(stampPath(outDir), "utf8"));
  } catch (_) {
    return null;
  }
}

/** `{ fresh, reason }` — `fresh:false` means the tree must be rebuilt. */
function checkStamp(outDir, roots, label) {
  const stamp = readStamp(outDir);
  if (!stamp || typeof stamp.hash !== "string") {
    return {
      fresh: false,
      reason: `${label} has no build stamp — it predates this check or was built by hand.`,
    };
  }
  const current = hashSources(roots);
  if (current !== stamp.hash) {
    return {
      fresh: false,
      reason: `${label} was built from different sources (built ${stamp.builtAt}); sources have changed since.`,
    };
  }
  return { fresh: true, reason: "" };
}

const repoRoot = path.resolve(__dirname, "..", "..");

/** The source trees each packaged tree is built from. */
const SOURCE_ROOTS = {
  backend: [{ label: "backend/app", dir: path.join(repoRoot, "backend", "app") }],
  frontend: [
    { label: "frontend/src", dir: path.join(repoRoot, "frontend", "src") },
    { label: "frontend/public", dir: path.join(repoRoot, "frontend", "public") },
    // Config changes alter the built output as much as source edits do.
    { label: "frontend/next.config.ts", dir: path.join(repoRoot, "frontend", "next.config.ts") },
  ],
};

module.exports = { STAMP_FILE, SOURCE_ROOTS, hashSources, writeStamp, readStamp, checkStamp };
