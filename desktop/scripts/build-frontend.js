#!/usr/bin/env node
/**
 * Build Next.js standalone output into desktop/resources/frontend.
 *
 * electron-builder silently drops folders named `node_modules` from
 * extraResources, so we rename the standalone deps to `node_deps` and boot
 * via run-server.js (sets NODE_PATH).
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { apiUrl } = require("../ports");
const { writeStamp, SOURCE_ROOTS } = require("./source-stamp");

const repoRoot = path.resolve(__dirname, "..", "..");
const frontendDir = path.join(repoRoot, "frontend");
const outDir = path.join(__dirname, "..", "resources", "frontend");

function rmrf(dir) {
  fs.rmSync(dir, { recursive: true, force: true });
}

function copyRecursive(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.cpSync(src, dest, { recursive: true });
}

function findServerJs(root) {
  const direct = path.join(root, "server.js");
  if (fs.existsSync(direct)) return direct;
  const nested = path.join(root, "frontend", "server.js");
  if (fs.existsSync(nested)) return nested;
  return null;
}

function writeRunServer(destDir) {
  const script = `#!/usr/bin/env node
/** Boot Next standalone with deps living in ./node_deps (not node_modules). */
const path = require("path");
const Module = require("module");
const deps = path.join(__dirname, "node_deps");
process.env.NODE_PATH = [deps, process.env.NODE_PATH || ""]
  .filter(Boolean)
  .join(path.delimiter);
Module._initPaths();
require("./server.js");
`;
  fs.writeFileSync(path.join(destDir, "run-server.js"), script, "utf8");
}

function main() {
  console.log("Building frontend standalone…");
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  execFileSync(npm, ["ci"], { cwd: frontendDir, stdio: "inherit", shell: true });
  execFileSync(npm, ["run", "build"], {
    cwd: frontendDir,
    stdio: "inherit",
    shell: true,
    env: {
      ...process.env,
      NEXT_PUBLIC_API_URL: "/api/v1",
      API_PROXY_TARGET: apiUrl(),
      FILES_PROXY_TARGET: apiUrl(),
      NODE_ENV: "production",
    },
  });

  const standalone = path.join(frontendDir, ".next", "standalone");
  const staticDir = path.join(frontendDir, ".next", "static");
  const publicDir = path.join(frontendDir, "public");

  const serverJs = findServerJs(standalone);
  if (!serverJs) {
    throw new Error(
      "Missing .next/standalone/server.js — check next.config output: standalone",
    );
  }

  rmrf(outDir);
  fs.mkdirSync(outDir, { recursive: true });

  const serverDir = path.dirname(serverJs);
  if (serverDir === standalone) {
    copyRecursive(standalone, outDir);
  } else {
    copyRecursive(serverDir, outDir);
    const nm = path.join(standalone, "node_modules");
    if (fs.existsSync(nm) && !fs.existsSync(path.join(outDir, "node_modules"))) {
      copyRecursive(nm, path.join(outDir, "node_modules"));
    }
  }

  copyRecursive(staticDir, path.join(outDir, ".next", "static"));
  if (fs.existsSync(publicDir)) {
    copyRecursive(publicDir, path.join(outDir, "public"));
  }

  if (!fs.existsSync(path.join(outDir, "server.js"))) {
    throw new Error("Frontend bundle missing server.js after copy");
  }
  if (!fs.existsSync(path.join(outDir, "node_modules", "next"))) {
    throw new Error(
      "Frontend bundle missing node_modules/next — standalone copy incomplete",
    );
  }

  // Avoid electron-builder's default node_modules exclusion.
  const nmOut = path.join(outDir, "node_modules");
  const depsOut = path.join(outDir, "node_deps");
  rmrf(depsOut);
  fs.renameSync(nmOut, depsOut);
  writeRunServer(outDir);

  if (!fs.existsSync(path.join(depsOut, "next"))) {
    throw new Error("Frontend bundle missing node_deps/next after rename");
  }

  // Record which sources produced this bundle so a later packaging run can
  // refuse to ship a UI that no longer matches frontend/src.
  const stamp = writeStamp(outDir, SOURCE_ROOTS.frontend);
  console.log("Frontend standalone ready at", outDir);
  console.log("  source stamp", stamp.hash.slice(0, 16));
}

main();
