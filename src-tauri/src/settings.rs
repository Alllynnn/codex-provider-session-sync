use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{env, fs, path::PathBuf};

pub const DEFAULT_INTERVAL_SECONDS: u64 = 300;
pub const MIN_INTERVAL_SECONDS: u64 = 30;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncSettings {
    pub enabled: bool,
    pub interval_seconds: u64,
    pub providers: Vec<String>,
    pub codex_home: PathBuf,
    pub log_file: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PersistedSettings {
    enabled: bool,
    interval_seconds: u64,
    providers: Vec<String>,
}

impl Default for PersistedSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            interval_seconds: DEFAULT_INTERVAL_SECONDS,
            providers: default_providers(),
        }
    }
}

pub fn default_providers() -> Vec<String> {
    vec!["openai".into(), "openrouter".into(), "custom".into()]
}

pub fn default_codex_home() -> PathBuf {
    env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|home| home.join(".codex")))
        .unwrap_or_else(|| PathBuf::from(".codex"))
}

pub fn default_backup_dir() -> PathBuf {
    dirs::desktop_dir()
        .or_else(dirs::home_dir)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("codex-provider-session-sync-backup")
}

pub fn log_file(codex_home: &PathBuf) -> PathBuf {
    codex_home.join("log").join("provider-sync-daemon.log")
}

pub fn settings_path(codex_home: &PathBuf) -> PathBuf {
    codex_home.join("provider-session-sync.json")
}

pub fn load_settings() -> SyncSettings {
    let codex_home = default_codex_home();
    let persisted = fs::read_to_string(settings_path(&codex_home))
        .ok()
        .and_then(|text| serde_json::from_str::<PersistedSettings>(&text).ok())
        .unwrap_or_default();

    normalize(persisted, codex_home)
}

pub fn save_settings(settings: &SyncSettings) -> Result<()> {
    let persisted = PersistedSettings {
        enabled: settings.enabled,
        interval_seconds: settings.interval_seconds.max(MIN_INTERVAL_SECONDS),
        providers: clean_providers(settings.providers.clone()),
    };
    let path = settings_path(&settings.codex_home);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create settings dir: {}", parent.display()))?;
    }
    fs::write(path, serde_json::to_string_pretty(&persisted)? + "\n").context("write settings")
}

pub fn update_enabled(enabled: bool) -> Result<SyncSettings> {
    let mut settings = load_settings();
    settings.enabled = enabled;
    save_settings(&settings)?;
    Ok(settings)
}

pub fn update_interval(interval_seconds: u64) -> Result<SyncSettings> {
    let mut settings = load_settings();
    settings.interval_seconds = interval_seconds.max(MIN_INTERVAL_SECONDS);
    save_settings(&settings)?;
    Ok(settings)
}

fn normalize(mut persisted: PersistedSettings, codex_home: PathBuf) -> SyncSettings {
    persisted.interval_seconds = persisted.interval_seconds.max(MIN_INTERVAL_SECONDS);
    persisted.providers = clean_providers(persisted.providers);
    SyncSettings {
        enabled: persisted.enabled,
        interval_seconds: persisted.interval_seconds,
        providers: persisted.providers,
        log_file: log_file(&codex_home),
        codex_home,
    }
}

fn clean_providers(providers: Vec<String>) -> Vec<String> {
    let cleaned: Vec<String> = providers
        .into_iter()
        .map(|provider| provider.trim().to_string())
        .filter(|provider| !provider.is_empty())
        .collect();
    if cleaned.is_empty() {
        default_providers()
    } else {
        cleaned
    }
}
