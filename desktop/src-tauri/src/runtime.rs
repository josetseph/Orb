//! Spawns, watches and stops the Python desktop runtime, and owns the window.

use std::{
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

const INIT_JS: &str = include_str!("init.js");
const MAX_RESTARTS_PER_MINUTE: usize = 3;

#[derive(Default)]
pub struct Runtime {
    child: Mutex<Option<Child>>,
    quitting: AtomicBool,
    restarts: Mutex<Vec<Instant>>,
}

// ── Where things are ─────────────────────────────────────────────────────────

pub fn api_port() -> u16 {
    std::env::var("ORB_API_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(17401)
}

/// The UI: the API serves it. ORB_URL overrides for a Vite dev server.
pub fn app_url() -> String {
    std::env::var("ORB_URL").unwrap_or_else(|_| format!("http://127.0.0.1:{}", api_port()))
}

/// `~/Library/Application Support/Orb`, `%APPDATA%\Orb`, `~/.config/Orb` — the
/// folder the Electron shell and the Python runtime already agree on.
pub fn app_support_root(app: &AppHandle) -> PathBuf {
    app.path()
        .config_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("Orb")
}

pub fn paths_file(app: &AppHandle) -> PathBuf {
    std::env::var("ORB_PATHS_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| app_support_root(app).join("paths.json"))
}

pub fn read_paths(app: &AppHandle) -> serde_json::Value {
    fs::read_to_string(paths_file(app))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(serde_json::Value::Null)
}

/// A missing or unreadable paths.json re-opens setup rather than booting defaults.
pub fn first_run(app: &AppHandle) -> bool {
    std::env::var("ORB_SKIP_WIZARD").is_err() && !read_paths(app).is_object()
}

pub fn data_dir(app: &AppHandle) -> PathBuf {
    read_paths(app)
        .get("data_dir")
        .and_then(|v| v.as_str())
        .map(PathBuf::from)
        .unwrap_or_else(|| app_support_root(app).join("data"))
}

pub fn default_models_dir(app: &AppHandle) -> PathBuf {
    app_support_root(app).join("models")
}

pub struct Layout {
    pub backend: PathBuf,
    pub python: PathBuf,
    /// Set when packaged: the API serves this Vite build.
    pub frontend: Option<PathBuf>,
    /// Set when packaged: the runtime seeds Firefly from `<resources>/firefly`.
    pub resources: Option<PathBuf>,
}

pub fn layout(app: &AppHandle) -> Layout {
    // Debug builds run the repo checkout unless told otherwise, so a stale
    // prepared tree can never shadow the sources being worked on.
    let use_resources = !cfg!(debug_assertions) || std::env::var("ORB_USE_RESOURCES").is_ok();
    if let Some(res) = app.path().resource_dir().ok().filter(|_| use_resources) {
        let backend = res.join("backend");
        if backend.join("app").is_dir() {
            let python = if cfg!(windows) {
                backend.join("python").join("python.exe")
            } else {
                backend.join("python").join("bin").join("python3")
            };
            return Layout {
                backend,
                python,
                frontend: Some(res.join("frontend")),
                resources: Some(res),
            };
        }
    }
    // Development: the repo checkout, with its venv when there is one.
    let root = std::env::var("ORB_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
        });
    let backend = root.join("backend");
    let venv = backend.join(".venv").join("bin").join("python");
    let python = std::env::var("ORB_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            if venv.exists() {
                venv
            } else {
                PathBuf::from("python3")
            }
        });
    Layout {
        backend,
        python,
        frontend: None,
        resources: None,
    }
}

/// Finder/Dock launches get a minimal PATH; ffmpeg etc. live in Homebrew dirs.
fn tool_path() -> String {
    let extras: &[&str] = if cfg!(target_os = "macos") {
        &["/opt/homebrew/bin", "/usr/local/bin"]
    } else if cfg!(windows) {
        &[]
    } else {
        &["/usr/local/bin", "/usr/bin"]
    };
    let current = std::env::var("PATH").unwrap_or_default();
    let sep = if cfg!(windows) { ";" } else { ":" };
    let mut parts: Vec<&str> = extras.to_vec();
    parts.extend(
        current
            .split(sep)
            .filter(|p| !p.is_empty() && !extras.contains(p)),
    );
    parts.join(sep)
}

// ── Child process ────────────────────────────────────────────────────────────

fn spawn(app: &AppHandle) -> std::io::Result<()> {
    let lay = layout(app);
    let logs = data_dir(app).join("logs");
    fs::create_dir_all(&logs)?;
    let log_path = logs.join("backend.log");
    if fs::metadata(&log_path).map(|m| m.len() > 10 * 1024 * 1024).unwrap_or(false) {
        let _ = fs::rename(&log_path, logs.join("backend.log.1"));
    }
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    let mut cmd = Command::new(&lay.python);
    cmd.args(["-m", "app.desktop_runtime"])
        .current_dir(&lay.backend)
        .env("PYTHONPATH", &lay.backend)
        .env("PATH", tool_path())
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    if let Some(frontend) = &lay.frontend {
        cmd.env("FRONTEND_DIR", frontend);
    }
    if let Some(resources) = &lay.resources {
        cmd.env("ORB_RESOURCES_ROOT", resources);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // one group kill takes the runtime and its sidecars
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000 | 0x0000_0200); // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    }
    let child = cmd.spawn()?;
    *app.state::<Runtime>().child.lock().unwrap() = Some(child);
    Ok(())
}

fn child_pid(app: &AppHandle) -> Option<u32> {
    let state = app.state::<Runtime>();
    let guard = state.child.lock().unwrap();
    guard.as_ref().map(Child::id)
}

/// Some(status) once the child has exited.
fn child_exited(app: &AppHandle) -> Option<i32> {
    let state = app.state::<Runtime>();
    let mut guard = state.child.lock().unwrap();
    match guard.as_mut().map(Child::try_wait) {
        Some(Ok(Some(status))) => Some(status.code().unwrap_or(-1)),
        Some(Ok(None)) => None,
        Some(Err(_)) | None => Some(-1),
    }
}

fn kill_tree(pid: u32, force: bool) {
    #[cfg(unix)]
    {
        let sig = if force { "-KILL" } else { "-TERM" };
        let _ = Command::new("kill")
            .args([sig, &format!("-{pid}")])
            .status();
    }
    #[cfg(windows)]
    {
        let _ = force;
        let _ = Command::new("taskkill")
            .args(["/pid", &pid.to_string(), "/t", "/f"])
            .status();
    }
}

fn kill_child(app: &AppHandle) {
    let Some(pid) = child_pid(app) else { return };
    kill_tree(pid, false);
    let deadline = Instant::now() + Duration::from_secs(4);
    while child_exited(app).is_none() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    if child_exited(app).is_none() {
        kill_tree(pid, true);
    }
    *app.state::<Runtime>().child.lock().unwrap() = None;
}

// ── Boot / watch / stop ──────────────────────────────────────────────────────

pub fn boot(app: AppHandle) {
    let window = ensure_window(&app);
    if first_run(&app) {
        let _ = window.show(); // the bundled page shows the setup form
        return;
    }
    start(app);
}

/// Spawn the runtime and open the UI when it answers. Called on boot, after
/// setup, on restart, and when the runtime dies.
pub fn start(app: AppHandle) {
    if let Err(err) = spawn(&app) {
        fail(&app, &format!("Could not start the Python runtime: {err}"));
        return;
    }
    thread::spawn(move || watch(app));
}

fn watch(app: AppHandle) {
    let port = api_port();
    loop {
        if health_ok(port) {
            open_ui(&app);
            break;
        }
        if child_exited(&app).is_some() {
            fail(&app, "Orb's local services stopped before they were ready.");
            return;
        }
        thread::sleep(Duration::from_millis(150));
    }
    while child_exited(&app).is_none() {
        thread::sleep(Duration::from_millis(500));
    }
    let state = app.state::<Runtime>();
    if state.quitting.load(Ordering::SeqCst) {
        return;
    }
    let mut restarts = state.restarts.lock().unwrap();
    restarts.retain(|t| t.elapsed() < Duration::from_secs(60));
    if restarts.len() >= MAX_RESTARTS_PER_MINUTE {
        drop(restarts);
        fail(&app, "Orb's local services keep crashing.");
        return;
    }
    restarts.push(Instant::now());
    drop(restarts);
    *state.child.lock().unwrap() = None;
    start(app);
}

/// Kill the runtime and bring it back; the UI's "Backend offline" row calls this.
pub fn restart(app: AppHandle) {
    kill_child(&app);
    start(app);
}

pub fn stop(app: &AppHandle) {
    app.state::<Runtime>()
        .quitting
        .store(true, Ordering::SeqCst);
    kill_child(app);
}

fn health_ok(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    if stream
        .write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut buf = [0u8; 64];
    let n = stream.read(&mut buf).unwrap_or(0);
    String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.")
        && buf[..n].windows(5).any(|w| w == b" 200 ")
}

fn fail(app: &AppHandle, message: &str) {
    let log = data_dir(app).join("logs").join("backend.log");
    let tail = fs::read_to_string(&log)
        .map(|s| {
            s.lines()
                .rev()
                .take(12)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    let detail = format!("{message}\n\nLog: {}\n\n{tail}", log.display());
    let app2 = app.clone();
    let _ = app.run_on_main_thread(move || {
        use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
        app2.dialog()
            .message(detail)
            .title("Orb failed to start")
            .kind(MessageDialogKind::Error)
            .blocking_show();
    });
}

// ── Window ───────────────────────────────────────────────────────────────────

fn trusted(url: &Url) -> bool {
    matches!(url.scheme(), "tauri" | "asset")
        || url.host_str() == Some("tauri.localhost")
        || Url::parse(&app_url()).is_ok_and(|app| app.origin() == url.origin())
}

pub fn ensure_window(app: &AppHandle) -> WebviewWindow {
    if let Some(window) = app.get_webview_window("main") {
        return window;
    }
    let opener = app.clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Orb")
        .inner_size(1400.0, 900.0)
        .min_inner_size(900.0, 600.0)
        .visible(false)
        // Tauri's own drag-drop handler would swallow file drops before the
        // page sees them; the editor handles OS drops itself (upload into the
        // note), so the native handler stays off.
        .disable_drag_drop_handler()
        .initialization_script(INIT_JS)
        // Note content renders in this window: only the app may load in it,
        // everything else goes to the system browser.
        .on_navigation(move |url| {
            if trusted(url) {
                return true;
            }
            let _ = opener.opener().open_url(url.as_str(), None::<&str>);
            false
        })
        .build()
        .expect("main window")
}

/// Point the window at the UI (unless it is already there) and show it.
fn open_ui(app: &AppHandle) {
    let app = app.clone();
    let _ = app.clone().run_on_main_thread(move || {
        let window = ensure_window(&app);
        let target = app_url();
        let already = window
            .url()
            .map(|u| u.as_str().starts_with(&target))
            .unwrap_or(false);
        if !already {
            if let Ok(url) = Url::parse(&target) {
                let _ = window.navigate(url);
            }
        }
        let _ = window.show();
        let _ = window.set_focus();
    });
}

/// Dock click with no window: recreate it on the UI (or setup) at once.
#[cfg(target_os = "macos")]
pub fn show_main(app: &AppHandle) {
    if first_run(app) {
        let _ = ensure_window(app).show();
    } else {
        open_ui(app);
    }
}
