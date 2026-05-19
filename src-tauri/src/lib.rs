mod backup;
mod settings;
mod sync;

use backup::{create_snapshot, list_snapshots, restore_snapshot, BackupSnapshot};
use settings::{
    default_backup_dir, load_settings, save_settings, update_enabled, update_interval, SyncSettings,
};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use sync::{sync_all, SyncReport};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, State, WindowEvent,
};
#[cfg(target_os = "windows")]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

const RUN_KEY_NAME: &str = "CodexProviderSessionSync";

#[derive(Clone, Default)]
struct AppState {
    last_report: Arc<Mutex<Option<SyncReport>>>,
    background_started: Arc<AtomicBool>,
    operation_lock: Arc<Mutex<()>>,
}

fn append_log(settings: &SyncSettings, message: &str) {
    if let Some(parent) = settings.log_file.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&settings.log_file)
    {
        let _ = writeln!(
            file,
            "[{}] {}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            message
        );
    }
}

fn run_sync_cycle(state: &AppState) -> Result<SyncReport, String> {
    let _operation_guard = state.operation_lock.lock().map_err(|err| err.to_string())?;
    let settings = load_settings();
    let backup_dir = default_backup_dir();
    fs::create_dir_all(&backup_dir).map_err(|err| err.to_string())?;
    let snapshot = create_snapshot(&settings.codex_home, &backup_dir, "before-sync")
        .map_err(|err| err.to_string())?;
    match sync_all(&settings.codex_home, &backup_dir, &settings.providers, true) {
        Ok(mut report) => {
            report.backup_snapshot_id = Some(snapshot.id.clone());
            report.backup_snapshot_path = Some(snapshot.path.clone());
            append_log(
                &settings,
                &format!(
                    "sync ok: refreshed={} created={} conflicts={} backup={}",
                    report.mirror_refreshed,
                    report.mirror_created,
                    report.mirror_conflicts,
                    snapshot.id
                ),
            );
            *state.last_report.lock().map_err(|err| err.to_string())? = Some(report.clone());
            Ok(report)
        }
        Err(err) => {
            let mut report = state
                .last_report
                .lock()
                .map_err(|lock_err| lock_err.to_string())?
                .clone()
                .unwrap_or_default();
            report.last_error = Some(err.to_string());
            append_log(&settings, &format!("sync failed: {err}"));
            *state
                .last_report
                .lock()
                .map_err(|lock_err| lock_err.to_string())? = Some(report);
            Err(err.to_string())
        }
    }
}

fn start_background(app: AppHandle) {
    let state = app.state::<AppState>();
    if state.background_started.swap(true, Ordering::SeqCst) {
        return;
    }
    let state = app.state::<AppState>().inner().clone();
    thread::spawn(move || loop {
        let settings = load_settings();
        if settings.enabled {
            let _ = run_sync_cycle(&state);
        } else {
            append_log(&settings, "sync paused");
        }
        let interval = settings
            .interval_seconds
            .max(settings::MIN_INTERVAL_SECONDS);
        thread::sleep(Duration::from_secs(interval));
    });
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开面板", true, None::<&str>)?;
    let toggle = MenuItem::with_id(app, "toggle", "开启/关闭同步", true, None::<&str>)?;
    let interval = MenuItem::with_id(app, "interval", "设置同步间隔", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &toggle, &interval, &exit])?;

    TrayIconBuilder::new()
        .icon(Image::from_bytes(include_bytes!("../icons/icon.ico"))?)
        .tooltip("Codex Provider Session Sync")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" | "interval" => show_main_window(app),
            "toggle" => {
                let settings = load_settings();
                let _ = update_enabled(!settings.enabled);
            }
            "exit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg(target_os = "windows")]
fn run_key() -> Result<RegKey, String> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let path = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
    hkcu.create_subkey(path)
        .map(|(key, _)| key)
        .map_err(|err| err.to_string())
}

#[cfg(target_os = "windows")]
fn autostart_enabled_inner() -> bool {
    run_key()
        .and_then(|key| {
            key.get_value::<String, _>(RUN_KEY_NAME)
                .map_err(|err| err.to_string())
        })
        .is_ok()
}

#[cfg(not(target_os = "windows"))]
fn autostart_enabled_inner() -> bool {
    false
}

#[cfg(target_os = "windows")]
fn set_autostart_inner(enabled: bool) -> Result<bool, String> {
    let key = run_key()?;
    if enabled {
        let exe = std::env::current_exe().map_err(|err| err.to_string())?;
        key.set_value(RUN_KEY_NAME, &format!("\"{}\"", exe.display()))
            .map_err(|err| err.to_string())?;
        Ok(true)
    } else {
        let _ = key.delete_value(RUN_KEY_NAME);
        Ok(false)
    }
}

#[cfg(not(target_os = "windows"))]
fn set_autostart_inner(_enabled: bool) -> Result<bool, String> {
    Err("Autostart is only implemented on Windows.".into())
}

#[tauri::command]
fn get_settings() -> SyncSettings {
    load_settings()
}

#[tauri::command]
fn get_autostart_enabled() -> bool {
    autostart_enabled_inner()
}

#[tauri::command]
fn get_last_report(state: State<'_, AppState>) -> Option<SyncReport> {
    state
        .last_report
        .lock()
        .ok()
        .and_then(|report| report.clone())
}

#[tauri::command]
fn get_backup_snapshots() -> Result<Vec<BackupSnapshot>, String> {
    list_snapshots(&default_backup_dir()).map_err(|err| err.to_string())
}

#[tauri::command]
fn set_enabled(state: State<'_, AppState>, enabled: bool) -> Result<SyncSettings, String> {
    let _operation_guard = state.operation_lock.lock().map_err(|err| err.to_string())?;
    update_enabled(enabled).map_err(|err| err.to_string())
}

#[tauri::command]
fn set_interval(state: State<'_, AppState>, interval_seconds: u64) -> Result<SyncSettings, String> {
    let _operation_guard = state.operation_lock.lock().map_err(|err| err.to_string())?;
    update_interval(interval_seconds).map_err(|err| err.to_string())
}

#[tauri::command]
fn set_autostart(enabled: bool) -> Result<bool, String> {
    set_autostart_inner(enabled)
}

#[tauri::command]
fn sync_now(state: State<'_, AppState>) -> Result<SyncReport, String> {
    run_sync_cycle(&state)
}

#[tauri::command]
fn create_backup_now(state: State<'_, AppState>) -> Result<BackupSnapshot, String> {
    let _operation_guard = state.operation_lock.lock().map_err(|err| err.to_string())?;
    let settings = load_settings();
    let snapshot = create_snapshot(&settings.codex_home, &default_backup_dir(), "manual")
        .map_err(|err| err.to_string())?;
    append_log(&settings, &format!("backup created: {}", snapshot.id));
    Ok(snapshot)
}

#[tauri::command]
fn restore_backup(state: State<'_, AppState>, snapshot_id: String) -> Result<(), String> {
    let _operation_guard = state.operation_lock.lock().map_err(|err| err.to_string())?;
    let snapshots = list_snapshots(&default_backup_dir()).map_err(|err| err.to_string())?;
    let snapshot = snapshots
        .into_iter()
        .find(|snapshot| snapshot.id == snapshot_id)
        .ok_or_else(|| format!("Backup snapshot not found: {snapshot_id}"))?;
    let settings = load_settings();
    let rollback = create_snapshot(
        &settings.codex_home,
        &default_backup_dir(),
        "before-restore",
    )
    .map_err(|err| err.to_string())?;
    restore_snapshot(&snapshot).map_err(|err| err.to_string())?;
    append_log(
        &settings,
        &format!("backup restored: {} rollback={}", snapshot.id, rollback.id),
    );
    Ok(())
}

#[tauri::command]
fn open_log() -> Result<(), String> {
    let settings = load_settings();
    if let Some(parent) = settings.log_file.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    if !settings.log_file.exists() {
        fs::write(&settings.log_file, "").map_err(|err| err.to_string())?;
    }
    opener::open(settings.log_file).map_err(|err| err.to_string())
}

#[tauri::command]
fn get_log_tail() -> Result<String, String> {
    let settings = load_settings();
    if !settings.log_file.exists() {
        return Ok(String::new());
    }
    const TAIL_BYTES: u64 = 64 * 1024;
    let mut file = File::open(&settings.log_file).map_err(|err| err.to_string())?;
    let len = file.metadata().map_err(|err| err.to_string())?.len();
    let start = len.saturating_sub(TAIL_BYTES);
    file.seek(SeekFrom::Start(start))
        .map_err(|err| err.to_string())?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|err| err.to_string())?;
    let text = String::from_utf8_lossy(&bytes);
    let lines = text.lines().rev().take(80).collect::<Vec<_>>();
    Ok(lines.into_iter().rev().collect::<Vec<_>>().join("\n"))
}

#[tauri::command]
fn open_backup_dir() -> Result<(), String> {
    let dir = default_backup_dir();
    fs::create_dir_all(&dir).map_err(|err| err.to_string())?;
    opener::open(dir).map_err(|err| err.to_string())
}

#[tauri::command]
fn exit_app(app: AppHandle) {
    app.exit(0);
}

pub fn run() {
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            get_settings,
            get_autostart_enabled,
            get_last_report,
            get_backup_snapshots,
            set_enabled,
            set_interval,
            set_autostart,
            sync_now,
            create_backup_now,
            restore_backup,
            open_log,
            get_log_tail,
            open_backup_dir,
            exit_app
        ])
        .setup(|app| {
            let settings = load_settings();
            let _ = save_settings(&settings);
            build_tray(app.handle())?;
            start_background(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
