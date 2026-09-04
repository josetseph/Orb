#!/usr/bin/env node
/**
 * Fail fast before electron-builder runs.
 *
 * Checks three things, each of which has already shipped a broken app once:
 *   1. the packaged resource trees exist (prepare-dist was run at all);
 *   2. the bundled Python can actually import the backend's dependencies — a
 *      pip install that dies half way leaves a valid-looking python/bin/python3
 *      with an empty site-packages, and the app then hangs on the splash;
 *   3. every local module reachable from the shell entry points is covered by
 *      the `files` allow-list — a new require() that nobody adds to it
 *      produces "Cannot find module" the moment the packaged app starts;
 *   4. each packaged tree was built from the sources currently on disk —
 *      prepare-dist stops at the first failing stage, so a partial run leaves
 *      fresh and stale trees side by side and every other check passes.
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { checkStamp, SOURCE_ROOTS } = require("./source-stamp");

const desktopDir = path.join(__dirname, "..");
const root = path.join(desktopDir, "resources");
const win = process.platform === "win32";

const nodePath = win
  ? path.join(root, "node", "node.exe")
  : path.join(root, "node", "bin", "node");
const pythonCandidates = win
  ? [path.join(root, "backend", "python", "python.exe")]
  : [
      path.join(root, "backend", "python", "bin", "python3"),
      path.join(root, "backend", "python", "bin", "python"),
    ];

const failures = [];

// ── 1. Resource trees ───────────────────────────────────────────────────────
const required = [
  path.join(root, "backend", "app"),
  path.join(root, "frontend", "server.js"),
  path.join(root, "frontend", "run-server.js"),
  path.join(root, "frontend", "node_deps", "next"),
  path.join(root, "firefly", "app"),
  nodePath,
];
for (const p of required) {
  if (!fs.existsSync(p)) failures.push(`Missing resource: ${p}`);
}
const python = pythonCandidates.find((p) => fs.existsSync(p));
if (!python) failures.push(`Missing bundled Python: ${pythonCandidates[0]}`);

// ── 2. The bundled Python must be able to run the backend ───────────────────
// Mirrors bundle-python.js's validateImports; kept here because that script is
// where the install happens and this is where the packaging decision is made.
if (python) {
  const mods = [
    "uvicorn",
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "aiosqlite",
    "kuzu",
    "qdrant_client",
    "meilisearch",
    "llama_cpp",
    "greenlet",
  ];
  const script = `
import importlib, sys
missing = []
for m in ${JSON.stringify(mods)}:
    try:
        importlib.import_module(m)
    except Exception as exc:
        missing.append(f"{m}: {exc}")
if missing:
    print("\\n".join(missing))
    sys.exit(1)
print("ok")
`;
  try {
    execFileSync(python, ["-c", script], {
      cwd: path.join(root, "backend"),
      encoding: "utf8",
      timeout: 120000,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (err) {
    const detail = `${err.stdout || ""}${err.stderr || ""}`.trim();
    failures.push(
      "The bundled Python cannot import the backend's dependencies — the pip " +
        "step of prepare-dist did not finish.\n" +
        detail.split("\n").map((l) => `      ${l}`).join("\n"),
    );
  }
}

// ── 3. Local requires must be covered by the packaging allow-list ───────────
function globToRegExp(pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `^${escaped.replace(/\*\*\/\*/g, ".*").replace(/(?<!\.)\*/g, "[^/]*")}$`,
  );
}

/** Which `files` patterns include this project-relative path. */
function isPackaged(relPath, patterns) {
  let included = false;
  for (const pattern of patterns) {
    const negated = pattern.startsWith("!");
    const body = negated ? pattern.slice(1) : pattern;
    if (globToRegExp(body).test(relPath)) included = !negated;
  }
  return included;
}

/** Follow relative require()s from the shell entry points. */
function localRequires(entries) {
  const seen = new Set();
  const queue = [...entries];
  while (queue.length) {
    const file = queue.shift();
    const rel = path.relative(desktopDir, file);
    if (seen.has(rel) || !fs.existsSync(file)) continue;
    seen.add(rel);
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(/require\(\s*["'](\.[^"']+)["']\s*\)/g)) {
      let target = path.resolve(path.dirname(file), match[1]);
      if (!fs.existsSync(target) && fs.existsSync(`${target}.js`)) target += ".js";
      queue.push(target);
    }
  }
  return [...seen];
}

const { build } = JSON.parse(
  fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"),
);
const entryPoints = [
  path.join(desktopDir, build.main || "main.js"),
  path.join(desktopDir, "preload.js"),
];
// electron-builder always bundles package.json, whatever the patterns say.
const ALWAYS_PACKAGED = new Set(["package.json"]);

for (const rel of localRequires(entryPoints)) {
  if (ALWAYS_PACKAGED.has(rel)) continue;
  if (!isPackaged(rel, build.files)) {
    failures.push(
      `${rel} is require()d by the shell but no "files" pattern includes it — ` +
        "the packaged app would fail with \"Cannot find module\".",
    );
  }
}

// ── 4. Packaged trees must match the sources on disk ────────────────────────
for (const [tree, roots] of Object.entries(SOURCE_ROOTS)) {
  const outDir = path.join(root, tree === "frontend" ? "frontend" : "backend");
  if (!fs.existsSync(outDir)) continue; // already reported as missing above
  const { fresh, reason } = checkStamp(outDir, roots, `resources/${tree}`);
  if (!fresh) {
    failures.push(
      `${reason}\n      Packaging now would ship a stale ${tree}. ` +
        "Run: npm run prepare-dist",
    );
  }
}

// ── Report ──────────────────────────────────────────────────────────────────
if (failures.length) {
  console.error("Packaged resources are not ready:\n");
  for (const f of failures) console.error(` - ${f}`);
  console.error("\nRun: npm run prepare-dist");
  process.exit(1);
}
console.log("Packaged resources OK (trees, Python imports, shell modules)");
