/**
 * Process supervisor for Docker-free desktop: Qdrant, Meilisearch, FastAPI, Next.js.
 * Binaries are auto-downloaded into DATA_DIR/bin on first run (see download-binaries.js).
 */
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const os = require("os");
const { ensureBinaries, platformTriple } = require("./download-binaries");
const {
  PORTS,
  uiUrl,
  apiUrl,
  apiV1Url,
  fireflyUrl,
  qdrantUrl,
  meiliHealthUrl,
  corsOrigins,
} = require("./ports");
const { ensureFireflyRuntime } = require("./firefly-runtime");
const {
  getRepoRoot,
  getBackendDir,
  getFrontendDir,
  getPythonBinary,
  getNodeBinary,
  appSupportRoot,
  defaultDataDir,
  defaultModelsDir,
  useProductionFrontend,
  isPackagedLayout,
  envFirst,
} = require("./paths");

function platformKey() {
  return platformTriple().key;
}

function defaultPathsFile() {
  return path.join(appSupportRoot(), "paths.json");
}

function loadPaths(appRoot) {
  const loc = envFirst("ORB_PATHS_FILE", "LIVEOS_PATHS_FILE") || defaultPathsFile();
  const repoRoot = getRepoRoot();
  let dataDir = isPackagedLayout()
    ? defaultDataDir()
    : path.join(repoRoot, "data");
  let modelsDir = isPackagedLayout()
    ? defaultModelsDir()
    : path.join(repoRoot, "backend", "models");
  let defaultVault = null;
  try {
    if (fs.existsSync(loc)) {
      const j = JSON.parse(fs.readFileSync(loc, "utf8"));
      if (j.data_dir) dataDir = j.data_dir;
      if (j.models_dir) modelsDir = j.models_dir;
      if (j.default_vault_path) defaultVault = j.default_vault_path;
    }
  } catch (err) {
    // Silently falling back to defaults here made a truncated/conflicted
    // paths.json look like total data loss (empty vault, empty indexes).
    console.error(`Unreadable paths file ${loc}: ${err.message}`);
  }
  return { pathsFile: loc, dataDir, modelsDir, defaultVault, appRoot: appRoot || repoRoot };
}

function needsWizard(pathsFile) {
  if (!fs.existsSync(pathsFile)) return true;
  // A corrupt file (truncated write, cloud-sync conflict copy) re-opens setup
  // so the user re-picks their real dirs instead of silently booting defaults.
  try {
    JSON.parse(fs.readFileSync(pathsFile, "utf8"));
    return false;
  } catch (_) {
    return true;
  }
}

function resolveBinary(repoRoot, dataDir, name) {
  const key = platformKey();
  const candidates = [
    path.join(dataDir, "bin", key, name),
    path.join(repoRoot, "desktop", "binaries", key, name),
    path.join(dataDir, "bin", name),
    path.join(repoRoot, "desktop", "binaries", name),
  ];
  if (process.platform === "win32") {
    candidates.unshift(
      path.join(dataDir, "bin", key, `${name}.exe`),
      path.join(repoRoot, "desktop", "binaries", key, `${name}.exe`),
    );
  }
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

function withNativeToolPath(env = {}) {
  // Finder/Dock launches often get a minimal PATH without Homebrew.
  // Prepend common dirs so ffmpeg/ffprobe resolve when the user has them installed.
  const extras =
    process.platform === "darwin"
      ? ["/opt/homebrew/bin", "/usr/local/bin"]
      : process.platform === "win32"
        ? []
        : ["/usr/local/bin", "/usr/bin"];
  const current = env.PATH || process.env.PATH || "";
  const parts = [...extras, ...String(current).split(path.delimiter).filter(Boolean)];
  const seen = new Set();
  const merged = [];
  for (const p of parts) {
    if (!p || seen.has(p)) continue;
    seen.add(p);
    merged.push(p);
  }
  return { ...env, PATH: merged.join(path.delimiter) };
}

function serviceLogPath(dataDir, label) {
  const dir = path.join(dataDir, "logs");
  fs.mkdirSync(dir, { recursive: true });
  const safe = label.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return path.join(dir, `${safe}.log`);
}

function tailLog(logPath, maxLines = 24) {
  try {
    if (!logPath || !fs.existsSync(logPath)) return "";
    const lines = fs.readFileSync(logPath, "utf8").split(/\r?\n/);
    return lines.slice(-maxLines).join("\n").trim();
  } catch (_) {
    return "";
  }
}

function waitHttp(url, timeoutMs = 120000, child = null, logPath = null) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (child && child.exitCode !== null) {
        const tail = tailLog(logPath);
        reject(
          new Error(
            `Process exited before ${url} was ready${tail ? `\n\n${tail}` : ""}`,
          ),
        );
        return;
      }
      const req = http.get(url, { timeout: 2000 }, (res) => {
        res.resume();
        // A 5xx here is a service that bound its port but is broken —
        // resolving on it closed the splash onto a dead app.
        if (res.statusCode && res.statusCode < 500) {
          resolve();
          return;
        }
        if (Date.now() - start > timeoutMs) {
          const tail = tailLog(logPath);
          reject(
            new Error(
              `${url} responding with ${res.statusCode}${tail ? `\n\n${tail}` : ""}`,
            ),
          );
        } else setTimeout(tick, 1000);
      });
      req.on("error", () => {
        if (Date.now() - start > timeoutMs) {
          const tail = tailLog(logPath);
          reject(
            new Error(
              `Timeout waiting for ${url}${tail ? `\n\n${tail}` : ""}`,
            ),
          );
        } else setTimeout(tick, 1000);
      });
      req.on("timeout", () => {
        req.destroy();
        if (Date.now() - start > timeoutMs) {
          const tail = tailLog(logPath);
          reject(
            new Error(
              `Timeout waiting for ${url}${tail ? `\n\n${tail}` : ""}`,
            ),
          );
        } else setTimeout(tick, 1000);
      });
    };
    tick();
  });
}

function pidsListeningOnPort(port) {
  try {
    if (process.platform === "win32") {
      const { execSync } = require("child_process");
      const out = execSync(`netstat -ano | findstr :${port}`, {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      const pids = new Set();
      for (const line of out.split(/\r?\n/)) {
        if (!/LISTENING/i.test(line)) continue;
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (/^\d+$/.test(pid) && pid !== "0") pids.add(Number(pid));
      }
      return [...pids];
    }
    const { execSync } = require("child_process");
    const out = execSync(`lsof -t -nP -iTCP:${port} -sTCP:LISTEN`, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return out
      .split(/\s+/)
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0);
  } catch (_) {
    return [];
  }
}

function killPid(pid) {
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", String(pid), "/f", "/t"], { stdio: "ignore" });
      return;
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch (_) {
      /* ignore */
    }
    setTimeout(() => {
      try {
        process.kill(pid, 0);
        process.kill(pid, "SIGKILL");
      } catch (_) {
        /* already gone */
      }
    }, 800);
  } catch (_) {
    /* ignore */
  }
}

/**
 * Clear leftover listeners from a previous Orb run (orphaned after force-quit).
 * Children are spawned detached so they can outlive Electron if stopAll never runs.
 */
function freeDesktopPorts(onStatus) {
  const ports = [
    PORTS.api,
    PORTS.ui,
    PORTS.firefly,
    PORTS.qdrant,
    PORTS.meilisearch,
  ];
  const mine = process.pid;
  const killed = [];
  for (const port of ports) {
    for (const pid of pidsListeningOnPort(port)) {
      if (pid === mine) continue;
      killPid(pid);
      killed.push(`${port}→${pid}`);
    }
  }
  if (killed.length && onStatus) {
    onStatus(`Cleared stale processes on ports: ${killed.join(", ")}`);
  }
}

/**
 * Persist a Meili master key under DATA_DIR. Existing installs that already have
 * Meili data keep ``orb-dev-key`` for compatibility; fresh installs get a random key.
 */
function resolveMeiliMasterKey(dataDir) {
  const fromEnv = process.env.MEILI_MASTER_KEY;
  if (fromEnv && String(fromEnv).trim()) return String(fromEnv).trim();

  const keyFile = path.join(dataDir, "meili_master_key");
  if (fs.existsSync(keyFile)) {
    const existing = fs.readFileSync(keyFile, "utf8").trim();
    if (existing) return existing;
  }

  const meiliDb = path.join(dataDir, "meilisearch");
  let key = "orb-dev-key";
  try {
    if (!fs.existsSync(meiliDb) || fs.readdirSync(meiliDb).length === 0) {
      key = require("crypto").randomBytes(32).toString("base64url");
    }
  } catch (_) {
    key = "orb-dev-key";
  }
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(keyFile, `${key}\n`, { encoding: "utf8", mode: 0o600 });
  try {
    fs.chmodSync(keyFile, 0o600);
  } catch (_) {
    /* best effort on non-POSIX */
  }
  return key;
}

class Supervisor {
  constructor(appRoot, onStatus) {
    this.appRoot = appRoot;
    this.repoRoot = getRepoRoot();
    this.onStatus = onStatus || (() => {});
    this.children = [];
    this.paths = loadPaths(appRoot);
  }

  _spawn(label, cmd, args, opts = {}) {
    this.onStatus(`Starting ${label}…`);
    // Use numeric fds for logging — WriteStream objects are invalid with detached spawn.
    let logFd = null;
    let stdio = opts.stdio || "ignore";
    if (opts.logFile) {
      fs.mkdirSync(path.dirname(opts.logFile), { recursive: true });
      logFd = fs.openSync(opts.logFile, "a");
      stdio = ["ignore", logFd, logFd];
    }
    const child = spawn(cmd, args, {
      cwd: opts.cwd || this.appRoot,
      env: withNativeToolPath({ ...process.env, ...opts.env }),
      stdio,
      shell: opts.shell || false,
      detached: process.platform !== "win32",
    });
    // Child owns a dup of the fd; close our handle so we don't leak.
    if (logFd !== null) {
      try {
        fs.closeSync(logFd);
      } catch (_) {
        /* ignore */
      }
    }
    child.on("error", (err) => {
      this.onStatus(`${label} error: ${err.message}`);
    });
    child.on("exit", (code, signal) => {
      if (code !== 0 && code !== null) {
        this.onStatus(`${label} exited (${code}${signal ? `, ${signal}` : ""})`);
      }
    });
    this.children.push({ label, child, logFile: opts.logFile || null });
    return child;
  }

  async startSearchEngines() {
    const { dataDir } = this.paths;
    fs.mkdirSync(path.join(dataDir, "qdrant"), { recursive: true });
    fs.mkdirSync(path.join(dataDir, "meilisearch"), { recursive: true });

    try {
      const ensured = await ensureBinaries(dataDir, this.onStatus);
      this.ensuredBinaries = ensured;
    } catch (err) {
      this.onStatus(`Binary download failed: ${err.message}. Retrying from cache…`);
      this.ensuredBinaries = {};
    }

    const qdrant =
      (this.ensuredBinaries && this.ensuredBinaries.qdrant) ||
      resolveBinary(this.repoRoot, dataDir, "qdrant");
    const meili =
      (this.ensuredBinaries && this.ensuredBinaries.meilisearch) ||
      resolveBinary(this.repoRoot, dataDir, "meilisearch");

    if (qdrant) {
      const storage = path.join(dataDir, "qdrant");
      this._spawn("Qdrant", qdrant, [], {
        env: {
          QDRANT__STORAGE__STORAGE_PATH: storage,
          QDRANT__SERVICE__HTTP_PORT: String(PORTS.qdrant),
        },
        cwd: storage,
      });
      try {
        await waitHttp(qdrantUrl(), 60000);
      } catch (err) {
        this.onStatus(`Qdrant may still be starting: ${err.message}`);
      }
    } else {
      throw new Error(
        "Could not download or find Qdrant binary. Check network access.",
      );
    }

    if (meili) {
      const masterKey = resolveMeiliMasterKey(dataDir);
      this.meiliMasterKey = masterKey;
      this._spawn("Meilisearch", meili, [
        "--db-path",
        path.join(dataDir, "meilisearch"),
        "--http-addr",
        `127.0.0.1:${PORTS.meilisearch}`,
        "--master-key",
        masterKey,
      ]);
      try {
        await waitHttp(meiliHealthUrl(), 60000);
      } catch (err) {
        this.onStatus(`Meilisearch may still be starting: ${err.message}`);
      }
    } else {
      throw new Error(
        "Could not download or find Meilisearch binary. Check network access.",
      );
    }
  }

  async startBackend() {
    const { dataDir, modelsDir } = this.paths;
    const backendDir = getBackendDir();
    const python = getPythonBinary();
    const logFile = serviceLogPath(dataDir, "backend");
    const masterKey = this.meiliMasterKey || resolveMeiliMasterKey(dataDir);
    const child = this._spawn(
      "Backend",
      python,
      [
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(PORTS.api),
      ],
      {
        cwd: backendDir,
        logFile,
        env: {
          ORB_DATA_DIR: dataDir,
          ORB_MODELS_DIR: modelsDir,
          ORB_PATHS_FILE: this.paths.pathsFile,
          PYTHONPATH: backendDir,
          DATABASE_BACKEND: "sqlite",
          STORAGE_BACKEND: "local",
          QDRANT_HOST: "127.0.0.1",
          QDRANT_PORT: String(PORTS.qdrant),
          MEILI_HOST: "127.0.0.1",
          MEILI_PORT: String(PORTS.meilisearch),
          MEILI_MASTER_KEY: masterKey,
          FIREFLY_BASE_URL: fireflyUrl(),
          FIREFLY_RUNTIME_FILE: path.join(dataDir, "firefly", "runtime.json"),
          // Whisper/Marlin load in-process in the API — no sidecar URLs.
          AI_SETUP_MODE: process.env.AI_SETUP_MODE || "none",
          LLM_PROVIDER: process.env.LLM_PROVIDER || "local",
          EMBEDDING_PROVIDER: process.env.EMBEDDING_PROVIDER || "local",
          CORS_ORIGINS: process.env.CORS_ORIGINS || corsOrigins(),
          // GGUF context / generation (one model loaded at a time).
          ORB_LLAMA_N_CTX: envFirst("ORB_LLAMA_N_CTX", "LIVEOS_LLAMA_N_CTX") || "16384",
          // No default output cap — the API sizes max_tokens per call from the
          // context left after the prompt; a fixed cap truncated long extractions.
          ...(envFirst("ORB_LLAMA_MAX_TOKENS", "LIVEOS_LLAMA_MAX_TOKENS")
            ? {
                ORB_LLAMA_MAX_TOKENS: envFirst(
                  "ORB_LLAMA_MAX_TOKENS",
                  "LIVEOS_LLAMA_MAX_TOKENS",
                ),
              }
            : {}),
          ORB_LLAMA_SWA_FULL: envFirst("ORB_LLAMA_SWA_FULL", "LIVEOS_LLAMA_SWA_FULL") || "true",
          ORB_LLAMA_REPEAT_PENALTY:
            envFirst("ORB_LLAMA_REPEAT_PENALTY", "LIVEOS_LLAMA_REPEAT_PENALTY") || "1.12",
          ORB_LLAMA_PROMPT_RESERVE:
            envFirst("ORB_LLAMA_PROMPT_RESERVE", "LIVEOS_LLAMA_PROMPT_RESERVE") || "4096",
          ORB_EMBED_N_CTX: envFirst("ORB_EMBED_N_CTX", "LIVEOS_EMBED_N_CTX") || "8192",
          ORB_RERANK_N_CTX: envFirst("ORB_RERANK_N_CTX", "LIVEOS_RERANK_N_CTX") || "8192",
        },
      },
    );
    await waitHttp(`${apiUrl()}/health`, 120000, child, logFile);
  }

  async startFirefly() {
    const { dataDir } = this.paths;
    const logFile = serviceLogPath(dataDir, "firefly");
    const runtime = await ensureFireflyRuntime(dataDir, this.onStatus);
    const child = this._spawn(
      "Firefly III",
      runtime.php,
      ["artisan", "serve", "--host", "127.0.0.1", "--port", String(PORTS.firefly)],
      {
        cwd: runtime.appDir,
        logFile,
        env: {
          APP_URL: fireflyUrl(),
          HOME: runtime.dataRoot,
          USERPROFILE: runtime.dataRoot,
        },
      },
    );
    await waitHttp(fireflyUrl(), 120000, child, logFile);
  }

  async startMultimodalServices() {
    // No HTTP sidecars — Whisper/Marlin load in-process in the API.
    const { modelsDir } = this.paths;
    const whisper = path.join(modelsDir, "whisper-large-v3-turbo");
    if (!fs.existsSync(whisper)) {
      this.onStatus("Multimodal models not installed yet — in-process load deferred");
      return;
    }
    this.onStatus("Preparing in-process Whisper / Marlin…");
    const backendDir = getBackendDir();
    const python = getPythonBinary();
    try {
      await new Promise((resolve, reject) => {
        const child = spawn(
          python,
          [
            "-c",
            "from app.services.multimodal_services import ensure_multimodal_services; "
            + "import json; print(json.dumps(ensure_multimodal_services(install_deps=True, start_marlin=True)))",
          ],
          {
            cwd: backendDir,
            env: {
              ...process.env,
              ORB_MODELS_DIR: modelsDir,
              PYTHONPATH: backendDir,
            },
            stdio: ["ignore", "pipe", "pipe"],
          },
        );
        let out = "";
        let err = "";
        child.stdout.on("data", (d) => {
          out += d.toString();
        });
        child.stderr.on("data", (d) => {
          err += d.toString();
        });
        child.on("error", reject);
        child.on("exit", (code) => {
          if (code === 0) resolve(out);
          else reject(new Error(err || out || `exit ${code}`));
        });
      });
      this.onStatus("Multimodal runtime ready (in-process)");
    } catch (err) {
      this.onStatus(`Multimodal in-process prep deferred: ${err.message}`);
    }
  }

  async startFrontend() {
    // Same-origin /api/v1 via Next rewrites when production; absolute URL for next dev.
    const apiUrlPath = useProductionFrontend() ? "/api/v1" : apiV1Url();
    const frontendDir = getFrontendDir();
    const logFile = serviceLogPath(this.paths.dataDir, "frontend");
    let frontendChild;

    if (useProductionFrontend()) {
      const node = getNodeBinary();
      frontendChild = this._spawn("Frontend", node, ["run-server.js"], {
        cwd: frontendDir,
        logFile,
        env: {
          PORT: String(PORTS.ui),
          HOSTNAME: "127.0.0.1",
          NODE_ENV: "production",
          NEXT_PUBLIC_API_URL: apiUrlPath,
          API_PROXY_TARGET: apiUrl(),
          FILES_PROXY_TARGET: apiUrl(),
        },
      });
    } else {
      const npm = process.platform === "win32" ? "npm.cmd" : "npm";
      frontendChild = this._spawn("Frontend", npm, ["run", "dev", "--", "-p", String(PORTS.ui)], {
        cwd: frontendDir,
        logFile,
        env: {
          NEXT_PUBLIC_API_URL: apiUrlPath,
          API_PROXY_TARGET: apiUrl(),
          FILES_PROXY_TARGET: apiUrl(),
        },
        shell: process.platform === "win32",
      });
    }
    await waitHttp(uiUrl(), 120000, frontendChild, logFile);
  }

  async startAll() {
    // Previous force-quit leaves orphaned uvicorn/node/qdrant on our ports.
    freeDesktopPorts(this.onStatus);
    // Give SIGTERM a moment before we bind.
    await new Promise((r) => setTimeout(r, 900));
    await this.startSearchEngines();
    await this.startFirefly();
    // API + UI in parallel — biggest cold-start win after binaries are ready.
    this.onStatus("Starting API and UI…");
    await Promise.all([this.startBackend(), this.startFrontend()]);
    // Multimodal is optional and heavy — never block first window on it.
    this.startMultimodalServices().catch((err) => {
      this.onStatus(`Multimodal services deferred: ${err.message}`);
    });
    this.onStatus("Ready");
  }

  stopAll() {
    for (const { label, child } of this.children.reverse()) {
      try {
        if (process.platform === "win32") {
          spawn("taskkill", ["/pid", String(child.pid), "/f", "/t"]);
        } else if (child.pid) {
          try {
            process.kill(-child.pid, "SIGTERM");
          } catch (_) {
            child.kill("SIGTERM");
          }
          // Detached children become orphans if Electron is killed hard —
          // escalate after a short grace period.
          const pid = child.pid;
          setTimeout(() => {
            try {
              process.kill(-pid, "SIGKILL");
            } catch (_) {
              try {
                process.kill(pid, "SIGKILL");
              } catch (__) {
                /* ignore */
              }
            }
          }, 1500);
        }
      } catch (_) {
        try {
          child.kill("SIGTERM");
        } catch (__) {
          /* ignore */
        }
      }
      this.onStatus(`Stopped ${label}`);
    }
    this.children = [];
    // Belt-and-suspenders: free any leftover listeners on our port block.
    freeDesktopPorts();
  }

  /**
   * Awaitable shutdown for `before-quit`: SIGTERM everything, wait, SIGKILL
   * stragglers. The sync `stopAll` schedules its SIGKILL on a timer that never
   * fires once Electron exits, so children ignoring SIGTERM were orphaned.
   */
  async stopAllAsync(graceMs = 1500) {
    const children = this.children.reverse();
    this.children = [];
    for (const { label, child } of children) {
      try {
        if (process.platform === "win32") {
          spawn("taskkill", ["/pid", String(child.pid), "/f", "/t"]);
        } else if (child.pid) {
          try {
            process.kill(-child.pid, "SIGTERM");
          } catch (_) {
            child.kill("SIGTERM");
          }
        }
      } catch (_) {
        /* ignore */
      }
      this.onStatus(`Stopping ${label}…`);
    }
    if (process.platform !== "win32" && children.length) {
      await new Promise((r) => setTimeout(r, graceMs));
      for (const { child } of children) {
        const pid = child.pid;
        if (!pid) continue;
        try {
          process.kill(-pid, "SIGKILL");
        } catch (_) {
          try {
            process.kill(pid, "SIGKILL");
          } catch (__) {
            /* already gone */
          }
        }
      }
    }
    freeDesktopPorts();
  }
}

module.exports = {
  Supervisor,
  loadPaths,
  needsWizard,
  defaultPathsFile,
};
