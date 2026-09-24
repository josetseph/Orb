fn main() {
    // Naming the app's commands generates `allow-<command>` permissions. The
    // UI is served over loopback, a remote origin to Tauri, and a remote origin
    // may only invoke commands a capability grants it (capabilities/*.json).
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "app_state",
                "save_setup",
                "print_page",
                "restart_backend",
            ]),
        ),
    )
    .expect("tauri build");
}
