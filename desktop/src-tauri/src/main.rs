#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Orb desktop shell. One child process (the Python desktop runtime), one
//! window. First run shows the bundled setup page; every other launch opens
//! the UI the moment the API answers.

mod commands;
mod runtime;

use tauri::RunEvent;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(runtime::Runtime::default())
        .invoke_handler(tauri::generate_handler![
            commands::app_state,
            commands::save_setup,
            commands::restart_backend
        ])
        .setup(|app| {
            runtime::boot(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Orb")
        .run(|app, event| match event {
            // macOS keeps the app (and the local services) alive with no window,
            // so a Dock click reopens instantly instead of booting again.
            #[cfg(target_os = "macos")]
            RunEvent::ExitRequested { api, code, .. } => {
                if code.is_none() {
                    api.prevent_exit();
                }
            }
            #[cfg(target_os = "macos")]
            RunEvent::Reopen {
                has_visible_windows,
                ..
            } => {
                if !has_visible_windows {
                    runtime::show_main(app);
                }
            }
            RunEvent::Exit => runtime::stop(app),
            _ => {}
        });
}
