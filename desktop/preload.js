const { contextBridge, ipcRenderer } = require("electron");

/**
 * Bridge for the Next.js main window and for splash/wizard shell pages.
 * All windows use contextIsolation + this preload (no nodeIntegration).
 */
contextBridge.exposeInMainWorld("orbDesktop", {
  isDesktop: true,
  onStatus: (cb) => {
    ipcRenderer.on("status", (_event, message) => cb(message));
  },
  pickDirectory: (opts) => ipcRenderer.invoke("pick-directory", opts || {}),
  pickFile: (opts) => ipcRenderer.invoke("pick-file", opts || {}),
  // Cloud API keys: write-only from the renderer's point of view.
  listCredentials: () => ipcRenderer.invoke("credentials:list"),
  setCredential: (provider, apiKey) =>
    ipcRenderer.invoke("credentials:set", provider, apiKey),
  deleteCredential: (provider) => ipcRenderer.invoke("credentials:delete", provider),
  setEndpointCredential: (baseUrl, apiKey) =>
    ipcRenderer.invoke("credentials:set-endpoint", baseUrl, apiKey),
  deleteEndpointCredential: (baseUrl) =>
    ipcRenderer.invoke("credentials:delete-endpoint", baseUrl),
  getApiBaseUrl: () => ipcRenderer.invoke("get-api-base-url"),
  restartBackend: () => ipcRenderer.invoke("backend:restart"),
  revealInFolder: (filePath) =>
    ipcRenderer.invoke("reveal-in-folder", filePath),
  getDefaultPaths: () => ipcRenderer.invoke("get-default-paths"),
  getAppInfo: () => ipcRenderer.invoke("get-app-info"),
  saveWizard: (payload) => ipcRenderer.invoke("save-wizard", payload),
  wizardDone: () => ipcRenderer.send("wizard-done"),
});
