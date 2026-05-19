use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
};
use uuid::Uuid;
use walkdir::WalkDir;

const SYNC_NAMESPACE: Uuid = Uuid::from_u128(0x7c3bd33f77a84b6fb91e6f4236f26b4e);

#[derive(Debug, Clone)]
struct Session {
    path: PathBuf,
    session_id: String,
    provider: String,
    forked_from_id: Option<String>,
    line_count: usize,
    last_timestamp: Option<String>,
    size: u64,
    mtime: i64,
}

#[derive(Debug, Clone)]
struct MirrorPlan {
    source: Session,
    target_provider: String,
    mirror_id: String,
    mirror_path: PathBuf,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SyncReport {
    pub files_scanned: usize,
    pub provider_counts: BTreeMap<String, usize>,
    pub source_sessions: usize,
    pub mirror_needed: usize,
    pub mirror_created: usize,
    pub mirror_existing: usize,
    pub mirror_stale: usize,
    pub mirror_refreshed: usize,
    pub mirror_conflicts: usize,
    pub index_needed: usize,
    pub index_added: usize,
    pub index_stale: usize,
    pub index_updated: usize,
    pub last_run_at: Option<String>,
    pub last_error: Option<String>,
    pub backup_snapshot_id: Option<String>,
    pub backup_snapshot_path: Option<PathBuf>,
}

pub fn sync_all(codex_home: &Path, backup_dir: &Path, providers: &[String], apply: bool) -> Result<SyncReport> {
    let mut report = SyncReport::default();
    let sessions = collect_sessions(codex_home, &mut report)?;
    let plans = build_lineage_mirror_plans(&sessions, providers);
    report.source_sessions = plans.iter().map(|plan| plan.source.session_id.clone()).collect::<HashSet<_>>().len();
    apply_mirrors(&plans, codex_home, backup_dir, apply, &mut report)?;
    update_session_index(&plans, codex_home, backup_dir, apply, &mut report)?;
    report.last_run_at = Some(Utc::now().to_rfc3339());
    Ok(report)
}

fn iter_session_files(codex_home: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    for relative in ["sessions", "archived_sessions"] {
        let root = codex_home.join(relative);
        if !root.exists() {
            continue;
        }
        for entry in WalkDir::new(root).into_iter().filter_map(Result::ok) {
            if entry.file_type().is_file() && entry.path().extension().is_some_and(|ext| ext == "jsonl") {
                files.push(entry.path().to_path_buf());
            }
        }
    }
    files.sort();
    files
}

fn read_jsonl(path: &Path) -> Result<(Vec<String>, bool)> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let newline = text.ends_with('\n');
    Ok((text.lines().map(ToOwned::to_owned).collect(), newline))
}

fn get_session_meta(path: &Path) -> Result<Option<Map<String, Value>>> {
    let (lines, _) = read_jsonl(path)?;
    for (index, line) in lines.iter().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let payload: Value = serde_json::from_str(line).with_context(|| format!("invalid JSONL {}:{}", path.display(), index + 1))?;
        if payload.get("type").and_then(Value::as_str) == Some("session_meta") {
            return Ok(payload.get("payload").and_then(Value::as_object).cloned());
        }
    }
    Ok(None)
}

fn collect_sessions(codex_home: &Path, report: &mut SyncReport) -> Result<Vec<Session>> {
    let mut sessions = Vec::new();
    for path in iter_session_files(codex_home) {
        report.files_scanned += 1;
        let Some(meta) = get_session_meta(&path)? else {
            continue;
        };
        let provider = meta.get("model_provider").and_then(Value::as_str).unwrap_or("").trim().to_string();
        let provider_key = if provider.is_empty() { "<missing>".to_string() } else { provider.clone() };
        *report.provider_counts.entry(provider_key).or_insert(0) += 1;

        let session_id = meta.get("id").and_then(Value::as_str).unwrap_or("").trim().to_string();
        if provider.is_empty() || session_id.is_empty() {
            continue;
        }

        let (lines, _) = read_jsonl(&path)?;
        let mut last_timestamp = None;
        for line in &lines {
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(payload) = serde_json::from_str::<Value>(line) {
                if let Some(timestamp) = payload.get("timestamp").and_then(Value::as_str) {
                    last_timestamp = Some(timestamp.to_string());
                }
            }
        }
        let metadata = fs::metadata(&path)?;
        let mtime = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|duration| duration.as_secs() as i64)
            .unwrap_or_default();

        sessions.push(Session {
            path,
            session_id,
            provider,
            forked_from_id: meta.get("forked_from_id").and_then(Value::as_str).map(str::to_string),
            line_count: lines.len(),
            last_timestamp,
            size: metadata.len(),
            mtime,
        });
    }
    Ok(sessions)
}

fn lineage_key(session: &Session) -> String {
    session.forked_from_id.clone().unwrap_or_else(|| session.session_id.clone())
}

fn session_rank(session: &Session) -> (String, usize, u64, i64) {
    (
        session.last_timestamp.clone().unwrap_or_default(),
        session.line_count,
        session.size,
        session.mtime,
    )
}

fn choose_best_session(sessions: &[Session]) -> Option<Session> {
    sessions.iter().max_by_key(|session| session_rank(session)).cloned()
}

fn mirror_thread_id(source_id: &str, target_provider: &str) -> String {
    Uuid::new_v5(&SYNC_NAMESPACE, format!("{source_id}:{target_provider}").as_bytes()).to_string()
}

fn sanitize_filename_part(value: &str) -> String {
    let sanitized = value
        .chars()
        .map(|ch| if ch.is_alphanumeric() || matches!(ch, '.' | '_' | '-') { ch } else { '-' })
        .collect::<String>()
        .trim_matches(&['.', '-'][..])
        .to_string();
    if sanitized.is_empty() { "provider".into() } else { sanitized }
}

fn mirror_path(source_path: &Path, source_id: &str, mirror_id: &str, provider: &str) -> PathBuf {
    let filename = source_path.file_name().and_then(|name| name.to_str()).unwrap_or_default();
    if filename.contains(source_id) {
        return source_path.with_file_name(filename.replacen(source_id, mirror_id, 1));
    }
    let stem = source_path.file_stem().and_then(|name| name.to_str()).unwrap_or("session");
    let suffix = source_path.extension().and_then(|ext| ext.to_str()).map(|ext| format!(".{ext}")).unwrap_or_default();
    source_path.with_file_name(format!("{stem}--{}-{mirror_id}{suffix}", sanitize_filename_part(provider)))
}

fn build_lineage_mirror_plans(sessions: &[Session], providers: &[String]) -> Vec<MirrorPlan> {
    let provider_set = providers.iter().cloned().collect::<HashSet<_>>();
    let mut groups: HashMap<String, Vec<Session>> = HashMap::new();
    for session in sessions {
        if provider_set.contains(&session.provider) {
            groups.entry(lineage_key(session)).or_default().push(session.clone());
        }
    }

    let mut plans = Vec::new();
    let mut seen_targets = HashSet::new();
    for (root_id, group_sessions) in groups {
        let mut provider_sessions: HashMap<String, Session> = HashMap::new();
        for session in &group_sessions {
            let replace = provider_sessions
                .get(&session.provider)
                .map(|current| session_rank(session) > session_rank(current))
                .unwrap_or(true);
            if replace {
                provider_sessions.insert(session.provider.clone(), session.clone());
            }
        }
        let real_sources = group_sessions
            .iter()
            .filter(|session| session.forked_from_id.is_none())
            .cloned()
            .collect::<Vec<_>>();
        let Some(canonical) = choose_best_session(&real_sources) else {
            continue;
        };
        let root_session = group_sessions
            .iter()
            .find(|session| session.session_id == root_id)
            .cloned()
            .unwrap_or_else(|| canonical.clone());

        for provider in providers {
            let (mirror_id, target_path) = if let Some(target_session) = provider_sessions.get(provider) {
                if target_session.path == canonical.path {
                    continue;
                }
                (target_session.session_id.clone(), target_session.path.clone())
            } else {
                let mirror_id = mirror_thread_id(&root_id, provider);
                let source_id_for_path = if root_session.path.to_string_lossy().contains(&root_id) {
                    root_id.as_str()
                } else {
                    root_session.session_id.as_str()
                };
                let target_path = mirror_path(&root_session.path, source_id_for_path, &mirror_id, provider);
                (mirror_id, target_path)
            };
            if !seen_targets.insert((target_path.clone(), mirror_id.clone())) {
                continue;
            }
            plans.push(MirrorPlan { source: canonical.clone(), target_provider: provider.clone(), mirror_id, mirror_path: target_path });
        }
    }
    plans
}

fn replace_thread_ids(value: &mut Value, source_id: &str, mirror_id: &str) {
    match value {
        Value::Object(map) => {
            for (key, child) in map.iter_mut() {
                if matches!(key.as_str(), "id" | "thread_id" | "session_id" | "parent_thread_id" | "child_thread_id")
                    && child.as_str() == Some(source_id)
                {
                    *child = Value::String(mirror_id.to_string());
                } else {
                    replace_thread_ids(child, source_id, mirror_id);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                replace_thread_ids(item, source_id, mirror_id);
            }
        }
        _ => {}
    }
}

fn render_mirror(plan: &MirrorPlan) -> Result<String> {
    let (lines, newline_at_end) = read_jsonl(&plan.source.path)?;
    let mut rendered = Vec::new();
    let mut meta_seen = false;
    for (index, line) in lines.iter().enumerate() {
        if line.trim().is_empty() {
            rendered.push(line.clone());
            continue;
        }
        let mut payload: Value = serde_json::from_str(line).with_context(|| format!("invalid JSONL {}:{}", plan.source.path.display(), index + 1))?;
        replace_thread_ids(&mut payload, &plan.source.session_id, &plan.mirror_id);
        if payload.get("type").and_then(Value::as_str) == Some("session_meta") {
            let meta = payload.get_mut("payload").and_then(Value::as_object_mut).ok_or_else(|| anyhow!("session_meta payload is not an object"))?;
            meta.insert("id".into(), Value::String(plan.mirror_id.clone()));
            meta.insert("model_provider".into(), Value::String(plan.target_provider.clone()));
            meta.entry("forked_from_id").or_insert_with(|| Value::String(plan.source.session_id.clone()));
            meta_seen = true;
        }
        rendered.push(serde_json::to_string(&payload)?);
    }
    if !meta_seen {
        return Err(anyhow!("session_meta not found: {}", plan.source.path.display()));
    }
    let mut text = rendered.join("\n");
    if newline_at_end {
        text.push('\n');
    }
    Ok(text)
}

fn backup_file(path: &Path, codex_home: &Path, backup_dir: &Path) -> Result<()> {
    if !path.exists() {
        return Ok(());
    }
    let relative = path.strip_prefix(codex_home).unwrap_or(path);
    let destination = backup_dir.join(relative);
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    if !destination.exists() {
        fs::copy(path, destination)?;
    }
    Ok(())
}

fn inspect_existing_mirror(plan: &MirrorPlan) -> Result<bool> {
    let Some(meta) = get_session_meta(&plan.mirror_path)? else {
        return Ok(false);
    };
    Ok(meta.get("id").and_then(Value::as_str) == Some(&plan.mirror_id)
        && meta.get("model_provider").and_then(Value::as_str) == Some(&plan.target_provider))
}

fn apply_mirrors(plans: &[MirrorPlan], codex_home: &Path, backup_dir: &Path, apply: bool, report: &mut SyncReport) -> Result<()> {
    for plan in plans {
        if plan.mirror_path.exists() {
            if !inspect_existing_mirror(plan)? {
                report.mirror_conflicts += 1;
                continue;
            }
            let rendered = render_mirror(plan)?;
            let current = fs::read_to_string(&plan.mirror_path)?;
            if current == rendered {
                report.mirror_existing += 1;
                continue;
            }
            report.mirror_stale += 1;
            if apply {
                backup_file(&plan.mirror_path, codex_home, backup_dir)?;
                fs::write(&plan.mirror_path, rendered)?;
                report.mirror_refreshed += 1;
            }
            continue;
        }
        report.mirror_needed += 1;
        if apply {
            if let Some(parent) = plan.mirror_path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&plan.mirror_path, render_mirror(plan)?)?;
            report.mirror_created += 1;
        }
    }
    Ok(())
}

fn load_index(index_path: &Path) -> Result<(Vec<Value>, HashMap<String, Value>)> {
    if !index_path.exists() {
        return Ok((Vec::new(), HashMap::new()));
    }
    let mut items = Vec::new();
    let mut by_id = HashMap::new();
    for (index, line) in fs::read_to_string(index_path)?.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let item: Value = serde_json::from_str(line).with_context(|| format!("invalid session index {}:{}", index_path.display(), index + 1))?;
        if let Some(id) = item.get("id").and_then(Value::as_str) {
            by_id.insert(id.to_string(), item.clone());
        }
        items.push(item);
    }
    Ok((items, by_id))
}

fn fallback_index_item(plan: &MirrorPlan) -> Value {
    let updated_at = fs::metadata(&plan.source.path)
        .and_then(|metadata| metadata.modified())
        .ok()
        .map(DateTime::<Utc>::from)
        .unwrap_or_else(Utc::now)
        .to_rfc3339();
    serde_json::json!({
        "id": plan.mirror_id,
        "thread_name": plan.source.path.file_stem().and_then(|name| name.to_str()).unwrap_or("session"),
        "updated_at": updated_at
    })
}

fn update_session_index(plans: &[MirrorPlan], codex_home: &Path, backup_dir: &Path, apply: bool, report: &mut SyncReport) -> Result<()> {
    let index_path = codex_home.join("session_index.jsonl");
    let (mut items, by_id) = load_index(&index_path)?;
    let mut additions = Vec::new();
    let mut changed = false;

    for plan in plans {
        let mut item = by_id.get(&plan.source.session_id).cloned().unwrap_or_else(|| fallback_index_item(plan));
        if let Some(object) = item.as_object_mut() {
            object.insert("id".into(), Value::String(plan.mirror_id.clone()));
        }
        match by_id.get(&plan.mirror_id) {
            None => additions.push(item),
            Some(existing) if existing != &item => {
                report.index_stale += 1;
                if apply {
                    if let Some(existing_item) = items.iter_mut().find(|candidate| candidate.get("id").and_then(Value::as_str) == Some(&plan.mirror_id)) {
                        *existing_item = item;
                        changed = true;
                    }
                }
            }
            _ => {}
        }
    }
    report.index_needed = additions.len();
    if !apply || (!changed && additions.is_empty()) {
        return Ok(());
    }
    backup_file(&index_path, codex_home, backup_dir)?;
    items.extend(additions);
    let text = items.iter().map(serde_json::to_string).collect::<Result<Vec<_>, _>>()?.join("\n") + "\n";
    fs::write(index_path, text)?;
    report.index_added = report.index_needed;
    report.index_updated = report.index_stale;
    Ok(())
}
