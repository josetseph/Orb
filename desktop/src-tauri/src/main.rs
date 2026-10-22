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
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(runtime::Runtime::default())
        .invoke_handler(tauri::generate_handler![
            commands::app_state,
            commands::save_setup,
            commands::print_page,
            commands::restart_backend,
            commands::check_update,
            commands::install_update
        ])
        .setup(|app| {
            // A panic in a macOS event callback cannot unwind and aborts the app
            // with no message anywhere; record where it happened.
            let log = runtime::data_dir(app.handle()).join("logs").join("shell.log");
            #[cfg(target_os = "macos")]
            runtime::log_objc_exceptions(log.clone());
            std::panic::set_hook(Box::new(move |info| {
                let trace = std::backtrace::Backtrace::force_capture();
                runtime::append_log(&log, &format!("panic: {info}\n{trace}"));
            }));
            // Before any webview exists: WebKit reads these when it starts.
            #[cfg(target_os = "macos")]
            runtime::enable_text_checking();
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
