/**
 * Auto-fetch Qdrant + Meilisearch into DATA_DIR/bin.
 * End users never place binaries manually.
 * Local LLM uses in-process llama-cpp-python (no llama-server binary).
 */
const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

const QDRANT_VERSION = process.env.ORB_QDRANT_VERSION || "v1.18.2";
const MEILI_VERSION = process.env.ORB_MEILI_VERSION || "v1.49.0";

function computeSha256(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", () => resolve(hash.digest("hex").toLowerCase()));
    stream.on("error", reject);
  });
}

const KNOWN_SHA256 = {
  // Pinned release checksums if configured
};

async function verifyChecksum(filePath, assetName) {
  const envKey =
    "ORB_SHA256_" + assetName.replace(/[^a-zA-Z0-9]/g, "_").toUpperCase();
  const expected = process.env[envKey] || KNOWN_SHA256[assetName];
  if (!expected) return;
  const actual = await computeSha256(filePath);
  if (actual !== expected.toLowerCase().trim()) {
    throw new Error(
      `Integrity check failed for ${assetName}: SHA-256 mismatch.\nExpected: ${expected}\nActual:   ${actual}`
    );
  }
}

function nativeArch() {
  if (process.platform === "darwin") {
    try {
      const hw = execFileSync("sysctl", ["-n", "hw.optional.arm64"], {
        encoding: "utf8",
      }).trim();
      if (hw === "1") return "arm64";
    } catch (_) {
      /* ignore */
    }
    try {
      const uname = execFileSync("uname", ["-m"], { encoding: "utf8" }).trim();
      if (uname === "arm64") return "arm64";
    } catch (_) {
      /* ignore */
    }
  }
  return process.arch === "arm64" ? "arm64" : "x64";
}

function platformTriple() {
  const arch = nativeArch();
  if (process.platform === "darwin") {
    return {
      key: arch === "arm64" ? "macos-arm64" : "macos-x86_64",
      qdrantAsset:
        arch === "arm64"
          ? `qdrant-aarch64-apple-darwin.tar.gz`
          : `qdrant-x86_64-apple-darwin.tar.gz`,
      meiliAsset:
        arch === "arm64"
          ? "meilisearch-macos-apple-silicon"
          : "meilisearch-macos-amd64",
      qdrantExe: "qdrant",
      meiliExe: "meilisearch",
    };
  }
  if (process.platform === "linux") {
    return {
      key: arch === "arm64" ? "linux-aarch64" : "linux-x86_64",
      qdrantAsset:
        arch === "arm64"
          ? `qdrant-aarch64-unknown-linux-musl.tar.gz`
          : `qdrant-x86_64-unknown-linux-gnu.tar.gz`,
      meiliAsset:
        arch === "arm64" ? "meilisearch-linux-aarch64" : "meilisearch-linux-amd64",
      qdrantExe: "qdrant",
      meiliExe: "meilisearch",
    };
  }
  if (process.platform === "win32") {
    return {
      key: arch === "arm64" ? "windows-arm64" : "windows-amd64",
      qdrantAsset: `qdrant-x86_64-pc-windows-msvc.zip`,
      meiliAsset: "meilisearch-windows-amd64.exe",
      qdrantExe: "qdrant.exe",
      meiliExe: "meilisearch.exe",
    };
  }
  throw new Error(`Unsupported platform: ${process.platform}/${process.arch}`);
}

function downloadFile(url, dest, onProgress, redirectsLeft = 5) {
  // Open the write stream only after a 200 so GitHub 302→Azure redirects cannot
  // race an async unlink of the destination against the follow-up download.
  return new Promise((resolve, reject) => {
    const get = url.startsWith("https") ? https.get : http.get;
    const req = get(url, { timeout: 180000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        const next = new URL(res.headers.location, url).toString();
        if (redirectsLeft <= 0) {
          reject(new Error(`Too many redirects downloading ${url}`));
          return;
        }
        if (!next.startsWith("https:")) {
          // Never follow an https→http downgrade for executable payloads.
          reject(new Error(`Refusing non-https redirect: ${next}`));
          return;
        }
        downloadFile(next, dest, onProgress, redirectsLeft - 1).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`Download failed ${res.statusCode}: ${url}`));
        return;
      }
      const file = fs.createWriteStream(dest);
      const total = parseInt(res.headers["content-length"] || "0", 10);
      let received = 0;
      res.on("data", (chunk) => {
        received += chunk.length;
        if (onProgress && total) onProgress(Math.round((received / total) * 100));
      });
      res.pipe(file);
      file.on("finish", () =>
        file.close(() => {
          try {
            const size = fs.statSync(dest).size;
            if (total > 0 && size !== total) {
              reject(
                new Error(
                  `Incomplete download ${url}: got ${size} bytes, expected ${total}`,
                ),
              );
              return;
            }
            if (size <= 0) {
              reject(new Error(`Empty download: ${url}`));
              return;
            }
            resolve(dest);
          } catch (err) {
            reject(err);
          }
        }),
      );
      file.on("error", (err) => {
        try {
          fs.unlinkSync(dest);
        } catch (_) {
          /* ignore */
        }
        reject(err);
      });
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error(`Timeout downloading ${url}`));
    });
  });
}

function extractArchive(archivePath, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  execFileSync("tar", ["-xf", archivePath, "-C", destDir], { stdio: "ignore" });
}

function findExecutable(rootDir, name, depth = 0) {
  if (depth > 6) return null;
  const direct = path.join(rootDir, name);
  if (fs.existsSync(direct) && fs.statSync(direct).isFile()) return direct;
  let entries;
  try {
    entries = fs.readdirSync(rootDir, { withFileTypes: true });
  } catch (_) {
    return null;
  }
  for (const e of entries) {
    const p = path.join(rootDir, e.name);
    if (e.isFile() && (e.name === name || e.name === path.basename(name))) return p;
  }
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    if (e.name === "." || e.name === ".." || e.name === ".tmp") continue;
    const found = findExecutable(path.join(rootDir, e.name), name, depth + 1);
    if (found) return found;
  }
  return null;
}

function chmodExec(filePath) {
  if (process.platform === "win32") return;
  try {
    fs.chmodSync(filePath, 0o755);
  } catch (_) {
    /* ignore */
  }
}

function stripQuarantine(filePath) {
  if (process.platform !== "darwin") return;
  try {
    execFileSync("xattr", ["-dr", "com.apple.quarantine", filePath], {
      stdio: "ignore",
    });
  } catch (_) {
    /* ignore */
  }
}

/**
 * @param {string} dataDir
 * @param {(msg: string) => void} [onStatus]
 */
async function ensureBinaries(dataDir, onStatus) {
  const status = onStatus || (() => {});
  const triple = platformTriple();
  const binDir = path.join(dataDir, "bin", triple.key);
  fs.mkdirSync(binDir, { recursive: true });
  const qdrantPath = path.join(binDir, triple.qdrantExe);
  const meiliPath = path.join(binDir, triple.meiliExe);
  const tmpDir = path.join(dataDir, "bin", ".tmp");
  fs.mkdirSync(tmpDir, { recursive: true });

  if (!fs.existsSync(qdrantPath)) {
    status(`Downloading Qdrant ${QDRANT_VERSION}…`);
    const url = `https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/${triple.qdrantAsset}`;
    const archive = path.join(tmpDir, triple.qdrantAsset);
    let lastPct = -1;
    await downloadFile(url, archive, (pct) => {
      if (pct >= lastPct + 10) {
        lastPct = pct;
        status(`Downloading Qdrant… ${pct}%`);
      }
    });
    status("Verifying Qdrant checksum…");
    await verifyChecksum(archive, triple.qdrantAsset);
    status("Extracting Qdrant…");
    const extractTo = path.join(tmpDir, "qdrant-extract");
    fs.rmSync(extractTo, { recursive: true, force: true });
    fs.mkdirSync(extractTo, { recursive: true });
    extractArchive(archive, extractTo);
    const found = findExecutable(extractTo, triple.qdrantExe);
    if (!found) throw new Error(`Qdrant executable not found in ${triple.qdrantAsset}`);
    fs.copyFileSync(found, qdrantPath);
    chmodExec(qdrantPath);
    stripQuarantine(qdrantPath);
    fs.rmSync(archive, { force: true });
    fs.rmSync(extractTo, { recursive: true, force: true });
    status("Qdrant ready");
  }

  if (!fs.existsSync(meiliPath)) {
    status(`Downloading Meilisearch ${MEILI_VERSION}…`);
    const url = `https://github.com/meilisearch/meilisearch/releases/download/${MEILI_VERSION}/${triple.meiliAsset}`;
    const dest = path.join(tmpDir, triple.meiliAsset);
    let lastPct = -1;
    await downloadFile(url, dest, (pct) => {
      if (pct >= lastPct + 10) {
        lastPct = pct;
        status(`Downloading Meilisearch… ${pct}%`);
      }
    });
    status("Verifying Meilisearch checksum…");
    await verifyChecksum(dest, triple.meiliAsset);
    fs.copyFileSync(dest, meiliPath);
    chmodExec(meiliPath);
    stripQuarantine(meiliPath);
    fs.rmSync(dest, { force: true });
    status("Meilisearch ready");
  }

  return {
    qdrant: fs.existsSync(qdrantPath) ? qdrantPath : null,
    meilisearch: fs.existsSync(meiliPath) ? meiliPath : null,
    binDir,
  };
}

module.exports = {
  ensureBinaries,
  platformTriple,
  nativeArch,
  computeSha256,
  verifyChecksum,
  QDRANT_VERSION,
  MEILI_VERSION,
};
