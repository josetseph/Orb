const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const {
  Supervisor,
  needsWizard,
  defaultPathsFile,
  loadPaths,
} = require("./supervisor");
const {
  getAppRoot,
  isPackaged,
  defaultDataDir,
  defaultModelsDir,
  envFirst,
} = require("./paths");
const { uiUrl, apiV1Url } = require("./ports");
const credentialStore = require("./credentials");

// Keep macOS menu / "Quit …" label as Orb (not package.json "orb-desktop").
app.setName("Orb");
if (process.platform === "win32") {
  app.setAppUserModelId("com.orb.app");
}

const APP_URL = envFirst("ORB_URL", "LIVEOS_URL") || uiUrl();
const PRELOAD = path.join(__dirname, "preload.js");
const AI_SETUP_MODES = new Set(["none", "local", "cloud"]);

let mainWindow = null;
let splashWindow = null;
let wizardWindow = null;
let supervisor = null;
let quitting = false;

/**
 * The shell windows render user note content, so navigation is locked down:
 * only the local UI origin and the packaged file:// pages may load in-window;
 * everything else (including target=_blank) goes to the system browser.
 */
function isTrustedShellUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol === "file:") {
      return url.pathname.startsWith(__dirname);
    }
    const appOrigin = new URL(APP_URL).origin;
    return url.origin === appOrigin;
  } catch (_) {
    return false;
  }
}

function openExternally(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol === "https:" || url.protocol === "http:" || url.protocol === "mailto:") {
      shell.openExternal(rawUrl).catch(() => {});
    }
  } catch (_) {
    /* unparseable — drop */
  }
}

app.on("web-contents-created", (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    openExternally(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (!isTrustedShellUrl(url)) {
      event.preventDefault();
      openExternally(url);
    }
  });
});

/** Only the packaged wizard page may drive setup IPC. */
function isWizardSender(event) {
  const frameUrl = event.senderFrame?.url || "";
  return (
    wizardWindow &&
    !wizardWindow.isDestroyed() &&
    event.sender === wizardWindow.webContents &&
    frameUrl.startsWith("file:") &&
    frameUrl.endsWith("wizard.html")
  );
}

function sendStatus(message) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send("status", message);
  }
}

function appIconPath() {
  const candidates = [
    path.join(__dirname, "build", "icon.png"),
    path.join(__dirname, "assets", "logo.png"),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return undefined;
}

function shellWebPreferences() {
  return {
    preload: PRELOAD,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  };
}

function createSplash() {
  const icon = appIconPath();
  splashWindow = new BrowserWindow({
    width: 480,
    height: 360,
    resizable: false,
    title: "Orb",
    backgroundColor: "#0a0a0f",
    show: false,
    ...(icon ? { icon } : {}),
    webPreferences: shellWebPreferences(),
  });
  splashWindow.loadFile(path.join(__dirname, "splash.html"));
  splashWindow.once("ready-to-show", () => splashWindow.show());
  return splashWindow;
}

function createWizard() {
  const icon = appIconPath();
  const win = new BrowserWindow({
    width: 680,
    height: 820,
    minWidth: 560,
    minHeight: 640,
    title: "Orb Setup",
    backgroundColor: "#0a0a0f",
    ...(icon ? { icon } : {}),
    webPreferences: shellWebPreferences(),
  });
  win.loadFile(path.join(__dirname, "wizard.html"));
  wizardWindow = win;
  win.on("closed", () => {
    if (wizardWindow === win) wizardWindow = null;
  });
  return win;
}

function createMainWindow(initialPath = "/") {
  const icon = appIconPath();
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: "Orb",
    backgroundColor: "#0a0a0f",
    ...(icon ? { icon } : {}),
    webPreferences: shellWebPreferences(),
  });
  const url =
    initialPath && initialPath !== "/"
      ? `${APP_URL.replace(/\/$/, "")}${
          initialPath.startsWith("/") ? initialPath : `/${initialPath}`
        }`
      : APP_URL;
  loadWithRetry(mainWindow, url);
  // Without these, a renderer that dies or a navigation that fails leaves
  // Chromium's "This page couldn't load" with no record of why.
  mainWindow.webContents.on("render-process-gone", (_e, details) => {
    console.error(
      `Renderer gone: reason=${details.reason} exitCode=${details.exitCode}`,
    );
  });
  mainWindow.webContents.on(
    "did-fail-load",
    (_e, errorCode, desc, validatedURL, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return;
      console.error(`Load failed (${errorCode} ${desc}): ${validatedURL}`);
    },
  );
  mainWindow.on("unresponsive", () => {
    console.error("Window unresponsive — renderer is blocked or out of memory");
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

/** ERR_ABORTED (-3) is a navigation we replaced, not a failure worth retrying. */
function shouldRetryLoad(errorCode, isMainFrame) {
  return Boolean(isMainFrame) && errorCode !== -3;
}

/**
 * Load the app, retrying while the frontend is still coming up.
 *
 * The window opens before Next is listening, and a single loadURL leaves the
 * dead "This page couldn't load" screen up for good — the servers come up
 * seconds later and nothing goes back for them.
 */
function loadWithRetry(win, url, attempt = 0) {
  const maxAttempts = 40; // ~20s at 500ms
  win.webContents.once("did-fail-load", (_e, errorCode, desc, _u, isMainFrame) => {
    if (!shouldRetryLoad(errorCode, isMainFrame) || win.isDestroyed()) return;
    if (attempt >= maxAttempts) {
      console.warn(`Giving up loading ${url} after ${attempt} attempts: ${desc}`);
      return;
    }
    setTimeout(() => {
      if (!win.isDestroyed()) loadWithRetry(win, url, attempt + 1);
    }, 500);
  });
  win.loadURL(url);
}

function dirHasGguf(dir, depth = 0) {
  if (!dir || depth > 4 || !fs.existsSync(dir)) return false;
  try {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      let st;
      try {
        st = fs.statSync(p);
      } catch {
        continue;
      }
      if (st.isFile() && name.toLowerCase().endsWith(".gguf")) return true;
      if (st.isDirectory() && dirHasGguf(p, depth + 1)) return true;
    }
  } catch {
    return false;
  }
  return false;
}

function resolveBootPath() {
  // Full local without GGUFs yet → open Setup so download can auto-start.
  try {
    const pathsFile =
      envFirst("ORB_PATHS_FILE", "LIVEOS_PATHS_FILE") || defaultPathsFile();
    if (!fs.existsSync(pathsFile)) return "/";
    const j = JSON.parse(fs.readFileSync(pathsFile, "utf8"));
    const mode = String(
      j.ai_setup_mode || process.env.AI_SETUP_MODE || "none",
    ).toLowerCase();
    if (mode !== "local") return "/";
    const modelsDir = j.models_dir || process.env.MODELS_DIR;
    if (!dirHasGguf(modelsDir)) return "/setup";
  } catch (_) {
    /* ignore */
  }
  return "/";
}

function setupAutoUpdater() {
  // Opt-in only — unsigned builds must not hit GitHub Releases until Stage 6
  if (!isPackaged()) return;
  if (envFirst("ORB_ENABLE_UPDATER", "LIVEOS_ENABLE_UPDATER") !== "1") return;
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdater.autoDownload = false;
    autoUpdater.checkForUpdatesAndNotify().catch(() => {});
  } catch (_) {
    // electron-updater optional
  }
}

/** Require an absolute filesystem path (no null bytes). */
function assertAbsolutePath(label, value) {
  if (!value || typeof value !== "string") {
    throw new Error(`${label} is required`);
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.includes("\0")) {
    throw new Error(`${label} is invalid`);
  }
  if (!path.isAbsolute(trimmed)) {
    throw new Error(`${label} must be an absolute path`);
  }
  return path.resolve(trimmed);
}

function pathUnderRoot(candidate, root) {
  if (!root) return false;
  const resolved = path.resolve(candidate);
  const base = path.resolve(root);
  return resolved === base || resolved.startsWith(base + path.sep);
}

ipcMain.handle("pick-directory", async (event, opts = {}) => {
  // Parent window is required on macOS — otherwise the sheet can open behind
  // the wizard/main window and look like Browse "does nothing".
  const win =
    BrowserWindow.fromWebContents(event.sender) ||
    BrowserWindow.getFocusedWindow() ||
    undefined;
  const result = await dialog.showOpenDialog(win, {
    title: opts.title || "Choose folder",
    buttonLabel: opts.buttonLabel || "Select",
    properties: ["openDirectory", "createDirectory"],
    defaultPath: opts.defaultPath || undefined,
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

/**
 * Pick a single file (used to add a GGUF from outside the models directory).
 * Read-only: the renderer gets a path back, nothing is copied or executed here.
 */
ipcMain.handle("pick-file", async (event, opts = {}) => {
  const win =
    BrowserWindow.fromWebContents(event.sender) ||
    BrowserWindow.getFocusedWindow() ||
    undefined;
  const filters = Array.isArray(opts.filters) && opts.filters.length
    ? opts.filters
        .filter((f) => f && typeof f.name === "string" && Array.isArray(f.extensions))
        .map((f) => ({
          name: String(f.name).slice(0, 60),
          // Extensions are matched by Electron verbatim; keep them simple.
          extensions: f.extensions
            .filter((e) => typeof e === "string" && /^[A-Za-z0-9]{1,12}$/.test(e))
            .slice(0, 12),
        }))
        .filter((f) => f.extensions.length)
    : undefined;
  const result = await dialog.showOpenDialog(win, {
    title: opts.title || "Choose file",
    buttonLabel: opts.buttonLabel || "Select",
    properties: ["openFile"],
    defaultPath: opts.defaultPath || undefined,
    filters,
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle("get-api-base-url", () => apiV1Url());

ipcMain.handle("reveal-in-folder", (_e, filePath) => {
  if (!filePath || typeof filePath !== "string") {
    return { ok: false, error: "Missing path" };
  }
  let resolved;
  try {
    resolved = assertAbsolutePath("path", filePath);
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
  try {
    const roots = loadPaths(getAppRoot());
    const allowed = [
      roots.dataDir,
      roots.modelsDir,
      roots.defaultVault,
      defaultDataDir(),
      defaultModelsDir(),
    ];
    if (!allowed.some((root) => pathUnderRoot(resolved, root))) {
      return { ok: false, error: "Path is outside Orb data / vault / models" };
    }
    if (!fs.existsSync(resolved)) {
      return { ok: false, error: "Path does not exist" };
    }
    shell.showItemInFolder(resolved);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err?.message || err) };
  }
});

// ── Cloud API keys (OS keychain via safeStorage) ────────────────────────────
// The renderer can set and clear keys but never read them back.
//
// The store lives in the app's own userData directory, which is always local to
// this machine — DATA_DIR is frequently a synced folder (OneDrive, NAS), and
// uploading ciphertext only this machine can decrypt serves no purpose.
function credentialsDataDir() {
  return app.getPath("userData");
}

/**
 * Store a key, falling back to session-only when this machine cannot encrypt.
 *
 * Losing durable storage must not make a provider unusable: the key is still
 * pushed to the running API, so the app works until the next restart. Only
 * Linux without a keyring reaches this path — Windows (DPAPI) and macOS
 * (Keychain) always have a backend.
 */
async function storeAndPushCredential(id, apiKey) {
  const key = String(apiKey || "").trim();
  let persisted = true;
  let warning = "";
  try {
    credentialStore.saveCredential(credentialsDataDir(), id, key);
  } catch (err) {
    persisted = false;
    warning = String(err.message || err);
  }
  // Always push: a key that cannot be stored is still usable this session.
  await credentialStore.pushCredential(apiV1Url(), id, key);
  return { ok: true, persisted, warning };
}

ipcMain.handle("credentials:list", () => {
  try {
    return credentialStore.listCredentials(credentialsDataDir());
  } catch (err) {
    return { encryptionAvailable: false, providers: [], error: String(err.message || err) };
  }
});

ipcMain.handle("credentials:set", async (_event, provider, apiKey) => {
  try {
    const name = credentialStore.normalizeProvider(provider);
    if (!credentialStore.isKnownProvider(name)) {
      throw new Error(`Unknown provider '${provider}'`);
    }
    if (!String(apiKey || "").trim()) throw new Error("API key must not be empty");
    return { ...(await storeAndPushCredential(name, apiKey)), provider: name };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle("credentials:delete", async (_event, provider) => {
  try {
    const dataDir = credentialsDataDir();
    credentialStore.deleteCredential(dataDir, provider);
    await credentialStore.clearCredentialOnBackend(apiV1Url(), provider);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle("credentials:set-endpoint", async (_event, baseUrl, apiKey) => {
  try {
    const id = credentialStore.endpointCredentialId(baseUrl);
    // Servers such as llama-server accept any token; store a placeholder so the
    // endpoint is still remembered as configured.
    const key = String(apiKey || "").trim() || "not-needed";
    return {
      ...(await storeAndPushCredential(id, key)),
      baseUrl: id.slice(credentialStore.ENDPOINT_PREFIX.length),
    };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle("credentials:delete-endpoint", async (_event, baseUrl) => {
  try {
    const id = credentialStore.endpointCredentialId(baseUrl);
    credentialStore.deleteCredential(credentialsDataDir(), id);
    await credentialStore.clearCredentialOnBackend(apiV1Url(), id);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
});

ipcMain.handle("get-default-paths", () => ({
  data_dir: defaultDataDir(),
  models_dir: defaultModelsDir(),
}));

ipcMain.handle("get-app-info", () => {
  const pkg = require("./package.json");
  return {
    version: pkg.version,
    packaged: isPackaged(),
  };
});

ipcMain.handle("save-wizard", async (event, payload) => {
  if (!isWizardSender(event)) {
    // Renderer pages showing note content must never be able to repoint the
    // data dir — that path leads to executing binaries from arbitrary folders.
    throw new Error("save-wizard is only available to the setup wizard");
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("Invalid wizard payload");
  }
  const dataDir = assertAbsolutePath("data_dir", payload.data_dir);
  const modelsDir = assertAbsolutePath("models_dir", payload.models_dir);
  let defaultVault = undefined;
  if (payload.default_vault_path) {
    defaultVault = assertAbsolutePath(
      "default_vault_path",
      payload.default_vault_path,
    );
  }
  const aiMode = String(payload.ai_setup_mode || "none").toLowerCase();
  if (!AI_SETUP_MODES.has(aiMode)) {
    throw new Error("ai_setup_mode must be none, local, or cloud");
  }

  const loc = defaultPathsFile();
  fs.mkdirSync(path.dirname(loc), { recursive: true });
  const data = {
    data_dir: dataDir,
    models_dir: modelsDir,
    ai_setup_mode: aiMode,
  };
  if (defaultVault) data.default_vault_path = defaultVault;
  // Atomic write — a truncated paths.json used to boot the app with default
  // dirs and look like total data loss.
  const tmp = `${loc}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  fs.renameSync(tmp, loc);
  process.env.AI_SETUP_MODE = aiMode;

  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.mkdirSync(modelsDir, { recursive: true });
    if (defaultVault) fs.mkdirSync(defaultVault, { recursive: true });
    const rcPath = path.join(dataDir, "runtime_config.json");
    let rc = {};
    if (fs.existsSync(rcPath)) {
      try {
        rc = JSON.parse(fs.readFileSync(rcPath, "utf8"));
      } catch (_) {
        rc = {};
      }
    }
    rc.ai_setup_mode = aiMode;
    fs.writeFileSync(rcPath, JSON.stringify(rc, null, 2), "utf8");
  } catch (err) {
    console.warn("Could not write runtime_config.json", err);
  }
  return { ok: true, pathsFile: loc };
});

async function bootStack(appRoot) {
  supervisor = new Supervisor(appRoot, sendStatus);
  await supervisor.startAll();
  // The API holds keys in memory only, so they are re-pushed on every boot.
  try {
    // Earlier builds kept the store in DATA_DIR (often a synced folder).
    credentialStore.migrateLegacyStore(
      loadPaths(appRoot).dataDir,
      credentialsDataDir(),
    );
    const count = await credentialStore.pushAllCredentials(
      credentialsDataDir(),
      apiV1Url(),
    );
    if (count) sendStatus(`Restored ${count} saved API key(s)`);
  } catch (err) {
    console.error("Could not restore saved API keys:", err);
  }
}

app.whenReady().then(async () => {
  const appRoot = getAppRoot();
  const pathsFile =
    envFirst("ORB_PATHS_FILE", "LIVEOS_PATHS_FILE") || defaultPathsFile();

  if (
    needsWizard(pathsFile) &&
    !envFirst("ORB_SKIP_WIZARD", "LIVEOS_SKIP_WIZARD")
  ) {
    const wizard = createWizard();
    await new Promise((resolve) => {
      const onDone = (event) => {
        if (!isWizardSender(event)) return;
        ipcMain.removeListener("wizard-done", onDone);
        if (!wizard.isDestroyed()) wizard.close();
        resolve();
      };
      ipcMain.on("wizard-done", onDone);
    });
  }

  createSplash();
  try {
    await bootStack(appRoot);
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
    createMainWindow(resolveBootPath());
    setupAutoUpdater();
  } catch (err) {
    const message = String(err.message || err);
    sendStatus(message);
    console.error(err);
    await dialog.showMessageBox({
      type: "error",
      title: "Orb failed to start",
      message: "Could not start local services.",
      detail: `${message}\n\nLogs: ~/Library/Application Support/Orb/data/logs/`,
      buttons: ["Quit"],
      defaultId: 0,
    });
    if (supervisor) supervisor.stopAll();
    app.quit();
  }
});

app.on("window-all-closed", () => {
  // macOS: keep services running so reopening from the Dock works; stopping
  // them here left the app pointing at dead ports until a full relaunch.
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", (event) => {
  if (quitting || !supervisor) return;
  // Give children a real SIGTERM→SIGKILL window; a sync stop here races app
  // exit and leaves orphans that only die on the next launch's port sweep.
  quitting = true;
  event.preventDefault();
  Promise.resolve(supervisor.stopAllAsync?.() ?? supervisor.stopAll()).finally(
    () => app.exit(0),
  );
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createMainWindow(resolveBootPath());
  }
});
