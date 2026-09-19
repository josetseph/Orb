// Injected into every page in the Orb window (the bundled setup page and the
// UI served by the local API). Provides the same `window.orbDesktop` bridge the
// UI already uses, and keeps this single window on the app: anything that asks
// for a new window goes to the system browser instead.
(() => {
  const t = window.__TAURI__;
  if (!t) return;

  const openExternal = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (/^(https?|mailto):$/.test(url.protocol)) {
        t.opener.openUrl(url.href);
        return true;
      }
    } catch (_) {
      /* unparseable — drop */
    }
    return false;
  };
  window.open = (raw) => {
    if (raw) openExternal(raw);
    return null;
  };
  document.addEventListener(
    "click",
    (e) => {
      const a = e.target && e.target.closest && e.target.closest("a[href]");
      if (a && a.target === "_blank" && openExternal(a.href)) e.preventDefault();
    },
    true,
  );

  const dialogOpen = (options) =>
    t.dialog ? t.dialog.open(options) : t.core.invoke("plugin:dialog|open", { options });

  window.orbDesktop = {
    isDesktop: true,
    pickDirectory: (o = {}) =>
      dialogOpen({ directory: true, multiple: false, title: o.title, defaultPath: o.defaultPath }),
    pickFile: (o = {}) =>
      dialogOpen({
        directory: false,
        multiple: false,
        title: o.title,
        defaultPath: o.defaultPath,
        filters: o.filters,
      }),
    restartBackend: () =>
      t.core.invoke("restart_backend").then(
        () => ({ ok: true }),
        (err) => ({ ok: false, error: String(err) }),
      ),
    /** OS notification (macOS / Windows / Linux); asks for permission the first time. */
    notify: async (title, body) => {
      const n = t.notification;
      if (!n) return false;
      let granted = await n.isPermissionGranted();
      if (!granted) granted = (await n.requestPermission()) === "granted";
      if (granted) n.sendNotification({ title, body });
      return granted;
    },
  };
})();
