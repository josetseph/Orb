//! Commands the bundled setup page and the UI may call.

use std::{fs, path::Path};

use serde::Deserialize;
use serde_json::json;
use tauri::{AppHandle, Webview};

use crate::runtime;

/// What the bundled page needs to decide between "show setup" and "wait".
#[tauri::command]
pub fn app_state(app: AppHandle) -> serde_json::Value {
    json!({
        "first_run": runtime::first_run(&app),
        "data_dir": runtime::app_support_root(&app).join("data"),
        "models_dir": runtime::default_models_dir(&app),
        "version": app.package_info().version.to_string(),
    })
}

#[derive(Deserialize)]
pub struct SetupPayload {
    data_dir: String,
    models_dir: String,
    default_vault_path: Option<String>,
    ai_setup_mode: Option<String>,
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
    let mode = payload
        .ai_setup_mode
        .unwrap_or_else(|| "none".into())
        .to_lowercase();
    if !["none", "local", "cloud"].contains(&mode.as_str()) {
        return Err("ai_setup_mode must be none, local, or cloud".into());
    }

    let mut paths =
        json!({ "data_dir": data_dir, "models_dir": models_dir, "ai_setup_mode": mode });
    if let Some(v) = &vault {
        paths["default_vault_path"] = json!(v);
    }
    for dir in [Some(&data_dir), Some(&models_dir), vault.as_ref()]
        .into_iter()
        .flatten()
    {
        fs::create_dir_all(dir).map_err(|e| format!("Could not create {dir}: {e}"))?;
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

    // The API reads its AI mode from runtime_config.json in the data dir.
    let rc_path = Path::new(&data_dir).join("runtime_config.json");
    let mut rc: serde_json::Value = fs::read_to_string(&rc_path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| json!({}));
    rc["ai_setup_mode"] = json!(mode);
    let _ = fs::write(&rc_path, serde_json::to_string_pretty(&rc).unwrap());

    runtime::start(app);
    Ok(())
}

#[tauri::command]
pub fn restart_backend(app: AppHandle) -> Result<(), String> {
    if runtime::first_run(&app) {
        return Err("App is still being set up".into());
    }
    std::thread::spawn(move || runtime::restart(app));
    Ok(())
}
