//! Commands the bundled setup page and the UI may call.

use std::{fs, path::Path};

use serde::Deserialize;
use serde_json::json;
use tauri::{AppHandle, Manager, Webview};

use crate::runtime;

/// What the bundled page needs to decide between "show setup" and "wait".
#[tauri::command]
pub fn app_state(app: AppHandle) -> serde_json::Value {
    // Filled with the saved folders when setup is shown again after a failure.
    let saved = runtime::read_paths(&app);
    let saved_str = |key: &str| saved.get(key).and_then(|v| v.as_str()).map(str::to_string);
    json!({
        "first_run": runtime::setup_needed(&app),
        "data_dir": runtime::data_dir(&app),
        "models_dir": saved_str("models_dir").unwrap_or_else(|| runtime::default_models_dir(&app).display().to_string()),
        "vault_path": saved_str("default_vault_path"),
        "version": app.package_info().version.to_string(),
    })
}

#[derive(Deserialize)]
pub struct SetupPayload {
    data_dir: String,
    models_dir: String,
    default_vault_path: Option<String>,
}

fn absolute(label: &str, raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() || !Path::new(trimmed).is_absolute() {
        return Err(format!("{label} must be an absolute path"));
    }
    Ok(trimmed.to_string())
}

/// First-run setup: write paths.json, then start the runtime. Only the
/// bundled page may call this — the UI renders user note content and must not
/// be able to repoint the data dir at a folder it chose.
#[tauri::command]
pub fn save_setup(app: AppHandle, webview: Webview, payload: SetupPayload) -> Result<(), String> {
    let origin_ok = webview
        .url()
        .map(|u| matches!(u.scheme(), "tauri") || u.host_str() == Some("tauri.localhost"))
        .unwrap_or(false);
    if !origin_ok {
        return Err("setup is only available to the setup page".into());
    }
    let data_dir = absolute("data_dir", &payload.data_dir)?;
    let models_dir = absolute("models_dir", &payload.models_dir)?;
    let vault = match payload.default_vault_path.as_deref().map(str::trim) {
        Some(v) if !v.is_empty() => Some(absolute("default_vault_path", v)?),
        _ => None,
    };
    let mut paths = json!({ "data_dir": data_dir, "models_dir": models_dir });
    if let Some(v) = &vault {
        paths["default_vault_path"] = json!(v);
    }
    // Every folder must take a file now, not crash the runtime later: the
    // macOS picker opens on the Orb installer disk right after install.
    runtime::usable_dir("data", Path::new(&data_dir), true)?;
    runtime::usable_dir("models", Path::new(&models_dir), true)?;
    if let Some(v) = &vault {
        runtime::usable_dir("notes vault", Path::new(v), true)?;
    }
    // Atomic write: a truncated paths.json used to boot with default dirs and
    // look like total data loss.
    let file = runtime::paths_file(&app);
    if let Some(parent) = file.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let tmp = file.with_extension("json.tmp");
    fs::write(&tmp, serde_json::to_string_pretty(&paths).unwrap()).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &file).map_err(|e| e.to_string())?;

    app.state::<runtime::Runtime>()
        .setup_requested
        .store(false, std::sync::atomic::Ordering::SeqCst);
    runtime::start(app);
    Ok(())
}

/// The OS print dialog for the page as it is now (its PDF option is the note
/// export); the UI swaps in a print-only rendering first.
#[tauri::command]
pub fn print_page(webview: Webview) -> Result<(), String> {
    webview.print().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn restart_backend(app: AppHandle) -> Result<(), String> {
    if runtime::first_run(&app) {
        return Err("App is still being set up".into());
    }
    std::thread::spawn(move || runtime::restart(app));
    Ok(())
}
