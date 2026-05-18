use anyhow::{Context, Result};
use chrono::{Local, Utc};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};
use walkdir::WalkDir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackupSnapshot {
    pub id: String,
    pub created_at: String,
    pub codex_home: PathBuf,
    pub path: PathBuf,
    pub file_count: usize,
    pub total_bytes: u64,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct BackupManifest {
    id: String,
    created_at: String,
    codex_home: PathBuf,
    file_count: usize,
    total_bytes: u64,
    reason: String,
}

pub fn create_snapshot(codex_home: &Path, backup_root: &Path, reason: &str) -> Result<BackupSnapshot> {
    let created_at = Utc::now().to_rfc3339();
    let id = Local::now().format("%Y%m%d-%H%M%S").to_string();
    let snapshot_root = backup_root.join("snapshots").join(&id);
    fs::create_dir_all(&snapshot_root).with_context(|| format!("create snapshot {}", snapshot_root.display()))?;

    let mut file_count = 0;
    let mut total_bytes = 0;
    for relative_root in ["sessions", "archived_sessions"] {
        copy_tree(&codex_home.join(relative_root), &snapshot_root.join(relative_root), &mut file_count, &mut total_bytes)?;
    }
    copy_one(&codex_home.join("session_index.jsonl"), &snapshot_root.join("session_index.jsonl"), &mut file_count, &mut total_bytes)?;
    copy_one(&codex_home.join("provider-session-sync.json"), &snapshot_root.join("provider-session-sync.json"), &mut file_count, &mut total_bytes)?;

    let manifest = BackupManifest {
        id: id.clone(),
        created_at: created_at.clone(),
        codex_home: codex_home.to_path_buf(),
        file_count,
        total_bytes,
        reason: reason.to_string(),
    };
    fs::write(snapshot_root.join("manifest.json"), serde_json::to_string_pretty(&manifest)? + "\n")?;

    Ok(BackupSnapshot {
        id,
        created_at,
        codex_home: codex_home.to_path_buf(),
        path: snapshot_root,
        file_count,
        total_bytes,
        reason: reason.to_string(),
    })
}

pub fn list_snapshots(backup_root: &Path) -> Result<Vec<BackupSnapshot>> {
    let root = backup_root.join("snapshots");
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut snapshots = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let manifest_path = entry.path().join("manifest.json");
        let Ok(text) = fs::read_to_string(&manifest_path) else {
            continue;
        };
        let Ok(manifest) = serde_json::from_str::<BackupManifest>(&text) else {
            continue;
        };
        snapshots.push(BackupSnapshot {
            id: manifest.id,
            created_at: manifest.created_at,
            codex_home: manifest.codex_home,
            path: entry.path(),
            file_count: manifest.file_count,
            total_bytes: manifest.total_bytes,
            reason: manifest.reason,
        });
    }
    snapshots.sort_by(|left, right| right.created_at.cmp(&left.created_at));
    Ok(snapshots)
}

pub fn restore_snapshot(snapshot: &BackupSnapshot) -> Result<()> {
    for relative_root in ["sessions", "archived_sessions"] {
        let destination = snapshot.codex_home.join(relative_root);
        if destination.exists() {
            fs::remove_dir_all(&destination).with_context(|| format!("remove {}", destination.display()))?;
        }
        copy_tree(&snapshot.path.join(relative_root), &destination, &mut 0, &mut 0)?;
    }
    restore_optional_file(snapshot, "session_index.jsonl")?;
    restore_optional_file(snapshot, "provider-session-sync.json")?;
    Ok(())
}

fn restore_optional_file(snapshot: &BackupSnapshot, file_name: &str) -> Result<()> {
    let source = snapshot.path.join(file_name);
    let destination = snapshot.codex_home.join(file_name);
    if source.exists() {
        copy_one(&source, &destination, &mut 0, &mut 0)?;
    } else if destination.exists() {
        fs::remove_file(destination)?;
    }
    Ok(())
}

fn copy_tree(source: &Path, destination: &Path, file_count: &mut usize, total_bytes: &mut u64) -> Result<()> {
    if !source.exists() {
        return Ok(());
    }
    for entry in WalkDir::new(source).into_iter().filter_map(Result::ok) {
        if !entry.file_type().is_file() {
            continue;
        }
        let relative = entry.path().strip_prefix(source)?;
        copy_one(entry.path(), &destination.join(relative), file_count, total_bytes)?;
    }
    Ok(())
}

fn copy_one(source: &Path, destination: &Path, file_count: &mut usize, total_bytes: &mut u64) -> Result<()> {
    if !source.exists() {
        return Ok(());
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::copy(source, destination)?;
    *file_count += 1;
    *total_bytes += fs::metadata(source).map(|meta| meta.len()).unwrap_or_default();
    Ok(())
}
