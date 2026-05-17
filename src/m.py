from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


SYNC_NAMESPACE = uuid.UUID('7c3bd33f-77a8-4b6f-b91e-6f4236f26b4e')


@dataclass
class ConfigStatus:
    path: Path
    active_provider: str | None = None
    provider_keys: list[str] = field(default_factory=list)
    target_defined: bool = False
    sqlite_home: Path | None = None


@dataclass
class RolloutReport:
    files_scanned: int = 0
    files_needing_update: int = 0
    files_updated: int = 0
    session_meta_rewritten: int = 0
    provider_counts_before: Counter[str] = field(default_factory=Counter)
    provider_counts_after: Counter[str] = field(default_factory=Counter)


@dataclass
class SqliteReport:
    path: Path | None = None
    rows_needing_update: int = 0
    rows_updated: int = 0
    provider_counts_before: list[tuple[str | None, int]] = field(default_factory=list)
    provider_counts_after: list[tuple[str | None, int]] = field(default_factory=list)


@dataclass(frozen=True)
class SourceSession:
    path: Path
    thread_id: str
    provider: str


@dataclass(frozen=True)
class MirrorPlan:
    source_path: Path
    source_id: str
    target_provider: str
    mirror_id: str
    mirror_path: Path


@dataclass
class SyncReport:
    source_provider: str
    target_providers: list[str] = field(default_factory=list)
    files_scanned: int = 0
    source_sessions_found: int = 0
    mirror_files_needed: int = 0
    mirror_files_created: int = 0
    mirror_files_existing: int = 0
    mirror_file_conflicts: int = 0
    sqlite_rows_needed: int = 0
    sqlite_rows_created: int = 0
    sqlite_rows_existing: int = 0
    sqlite_rows_conflicting: int = 0
    sqlite_source_rows_missing: int = 0
    provider_counts_before: Counter[str] = field(default_factory=Counter)
    provider_counts_after: Counter[str] = field(default_factory=Counter)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='修复 Codex 历史会话的 provider 可见性。默认是旧的单目标迁移；'
        '加 --sync-openai-to-all-providers 后会把 openai 会话镜像到所有已配置 provider。',
    )
    parser.add_argument(
        '--codex-home',
        type=Path,
        default=default_codex_home(),
        help='Codex 数据目录，默认读取 CODEX_HOME 或 ~/.codex。',
    )
    parser.add_argument(
        '--target-provider',
        default='openai',
        help='单目标迁移后的 provider key，默认 openai；同步模式下不使用。',
    )
    parser.add_argument(
        '--keep-provider',
        action='append',
        default=None,
        help='单目标迁移时保留不迁移的 provider key，可重复传入；默认保留 openai 和目标 provider。',
    )
    parser.add_argument(
        '--sync-openai-to-all-providers',
        action='store_true',
        help='新增模式：保留 openai 原始会话，并为每个已配置 provider 创建可见镜像。',
    )
    parser.add_argument(
        '--source-provider',
        default='openai',
        help='同步模式的源 provider，默认 openai。',
    )
    parser.add_argument(
        '--sync-all-providers-mutually',
        action='store_true',
        help='Mutually sync sessions across configured providers by creating missing mirror sessions.',
    )
    parser.add_argument(
        '--sync-provider',
        action='append',
        default=None,
        help='同步模式的目标 provider，可重复传入；不传时使用 config.toml 中的 model_providers 加当前 active provider。',
    )
    parser.add_argument(
        '--state-db',
        type=Path,
        default=None,
        help='显式指定 state_*.sqlite 路径；默认自动发现。',
    )
    parser.add_argument(
        '--backup-dir',
        type=Path,
        default=None,
        help='执行写入时，把变更前的 SQLite 文件备份到该目录；镜像 JSONL 是新文件，不覆盖原文件。',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='执行真实写入；默认只预览。',
    )
    return parser.parse_args(argv)


def default_codex_home() -> Path:
    env_value = os.environ.get('CODEX_HOME')
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / '.codex'


def strip_toml_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False

    for index, char in enumerate(line):
        if in_double_quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_double_quote = False
            continue
        if in_single_quote:
            if char == "'":
                in_single_quote = False
            continue
        if char == '"':
            in_double_quote = True
        elif char == "'":
            in_single_quote = True
        elif char == '#':
            return line[:index]
    return line


def split_toml_dotted_name(raw_name: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for char in raw_name.strip():
        if quote == '"':
            if escaped:
                current.append(char)
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quote = None
            else:
                current.append(char)
            continue
        if quote == "'":
            if char == "'":
                quote = None
            else:
                current.append(char)
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '.':
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)

    parts.append(''.join(current).strip())
    return [part for part in parts if part]


def parse_toml_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if value == 'true':
        return True
    if value == 'false':
        return False
    return value


def parse_minimal_toml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current_table = root

    for raw_line in text.splitlines():
        line = strip_toml_comment(raw_line).strip()
        if not line:
            continue

        if line.startswith('[[') and line.endswith(']]'):
            table_name = line[2:-2].strip()
        elif line.startswith('[') and line.endswith(']'):
            table_name = line[1:-1].strip()
        else:
            table_name = ''

        if table_name:
            current_table = root
            for part in split_toml_dotted_name(table_name):
                child = current_table.setdefault(part, {})
                if not isinstance(child, dict):
                    child = {}
                    current_table[part] = child
                current_table = child
            continue

        if '=' not in line:
            continue

        raw_key, raw_value = line.split('=', 1)
        key_parts = split_toml_dotted_name(raw_key)
        if not key_parts:
            continue

        target = current_table
        for part in key_parts[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[key_parts[-1]] = parse_toml_scalar(raw_value)

    return root


def load_toml_config(config_path: Path) -> dict[str, Any]:
    text = config_path.read_text(encoding='utf-8')
    if text.startswith('\ufeff'):
        text = text.lstrip('\ufeff')
    if tomllib is not None:
        return tomllib.loads(text)
    return parse_minimal_toml(text)


def inspect_config(config_path: Path, target_provider: str) -> ConfigStatus:
    status = ConfigStatus(path=config_path)
    if not config_path.exists():
        return status
    data = load_toml_config(config_path)
    active_provider = data.get('model_provider')
    if isinstance(active_provider, str) and active_provider.strip():
        status.active_provider = active_provider.strip()
    providers = data.get('model_providers')
    if isinstance(providers, dict):
        status.provider_keys = sorted(str(key).strip() for key in providers.keys() if str(key).strip())
        status.target_defined = target_provider in providers
    sqlite_home = data.get('sqlite_home')
    if isinstance(sqlite_home, str) and sqlite_home.strip():
        status.sqlite_home = Path(sqlite_home).expanduser()
    return status


def resolve_sqlite_home_env() -> Path | None:
    raw = os.environ.get('CODEX_SQLITE_HOME')
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    path = Path(trimmed).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def iter_state_db_candidates(sqlite_home: Path) -> list[tuple[int, float, Path]]:
    version_pattern = re.compile(r'^state_(\d+)\.sqlite$')
    candidates: list[tuple[int, float, Path]] = []
    if not sqlite_home.exists():
        return candidates
    for path in sqlite_home.glob('state_*.sqlite'):
        match = version_pattern.match(path.name)
        if not match:
            continue
        if not is_sqlite_database(path):
            continue
        candidates.append((int(match.group(1)), path.stat().st_mtime, path))
    return candidates


def is_sqlite_database(path: Path) -> bool:
    try:
        return path.read_bytes()[:16] == b'SQLite format 3\x00'
    except OSError:
        return False


def resolve_state_db(codex_home: Path, config_status: ConfigStatus, explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return explicit_path
    search_roots: list[Path] = []
    for root in (config_status.sqlite_home, resolve_sqlite_home_env(), codex_home, codex_home / 'sqlite'):
        if root is None:
            continue
        if any(existing == root for existing in search_roots):
            continue
        search_roots.append(root)
    for root in search_roots:
        candidates = iter_state_db_candidates(root)
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
    return None


def iter_rollout_files(codex_home: Path) -> list[Path]:
    files: list[Path] = []
    for relative_dir in ('sessions', 'archived_sessions'):
        root = codex_home / relative_dir
        if not root.exists():
            continue
        files.extend(sorted(root.rglob('*.jsonl')))
    return files


def ensure_backup_root(backup_root: Path | None) -> Path | None:
    if backup_root is None:
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root


def backup_file(src: Path, codex_home: Path, backup_root: Path | None) -> None:
    if backup_root is None:
        return
    try:
        relative_path = src.relative_to(codex_home)
    except ValueError:
        relative_path = Path(src.name)
    destination = backup_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(src, destination)


def backup_sqlite_bundle(db_path: Path, backup_root: Path | None) -> None:
    if backup_root is None:
        return
    sqlite_root = backup_root / 'sqlite'
    sqlite_root.mkdir(parents=True, exist_ok=True)
    for suffix in ('', '-wal', '-shm'):
        current_path = Path(str(db_path) + suffix)
        if current_path.exists():
            destination = sqlite_root / current_path.name
            if not destination.exists():
                shutil.copy2(current_path, destination)


def read_jsonl(path: Path) -> tuple[list[str], bool]:
    original_text = path.read_text(encoding='utf-8-sig')
    return original_text.splitlines(), original_text.endswith('\n')


def json_dumps_line(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def get_session_meta(path: Path) -> tuple[int, dict[str, Any]] | None:
    lines, _ = read_jsonl(path)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'无法解析 JSONL: {path}:{line_number}: {exc}') from exc
        if not isinstance(payload, dict) or payload.get('type') != 'session_meta':
            continue
        session_meta = payload.get('payload')
        if not isinstance(session_meta, dict):
            raise ValueError(f'session_meta payload 不是对象: {path}:{line_number}')
        return line_number, session_meta
    return None


def rewrite_rollout_file(
    path: Path,
    codex_home: Path,
    target_provider: str,
    keep_providers: set[str],
    apply: bool,
    backup_root: Path | None,
    report: RolloutReport,
) -> None:
    original_lines, newline_at_end = read_jsonl(path)
    rewritten_lines: list[str] = []
    file_changed = False

    for line_number, line in enumerate(original_lines, start=1):
        if not line.strip():
            rewritten_lines.append(line)
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'无法解析 JSONL: {path}:{line_number}: {exc}') from exc

        if isinstance(payload, dict) and payload.get('type') == 'session_meta':
            session_meta = payload.get('payload')
            if not isinstance(session_meta, dict):
                raise ValueError(f'session_meta payload 不是对象: {path}:{line_number}')
            provider_value = session_meta.get('model_provider')
            provider_key = provider_value if isinstance(provider_value, str) and provider_value else '<missing>'
            report.provider_counts_before[provider_key] += 1
            simulated_provider = provider_key
            if isinstance(provider_value, str) and provider_value not in keep_providers:
                session_meta['model_provider'] = target_provider
                payload['payload'] = session_meta
                report.session_meta_rewritten += 1
                simulated_provider = target_provider
                file_changed = True
            report.provider_counts_after[simulated_provider] += 1
            line = json_dumps_line(payload)

        rewritten_lines.append(line)

    report.files_scanned += 1
    if not file_changed:
        return

    report.files_needing_update += 1
    if not apply:
        return

    backup_file(path, codex_home, backup_root)
    rewritten_text = '\n'.join(rewritten_lines)
    if newline_at_end:
        rewritten_text += '\n'
    path.write_text(rewritten_text, encoding='utf-8')
    report.files_updated += 1


def migrate_rollouts(
    codex_home: Path,
    target_provider: str,
    keep_providers: set[str],
    apply: bool,
    backup_root: Path | None,
) -> RolloutReport:
    report = RolloutReport()
    for path in iter_rollout_files(codex_home):
        rewrite_rollout_file(
            path=path,
            codex_home=codex_home,
            target_provider=target_provider,
            keep_providers=keep_providers,
            apply=apply,
            backup_root=backup_root,
            report=report,
        )
    return report


def fetch_sqlite_provider_counts(conn: sqlite3.Connection) -> list[tuple[str | None, int]]:
    rows = conn.execute(
        'SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider ORDER BY COUNT(*) DESC, model_provider ASC'
    ).fetchall()
    result: list[tuple[str | None, int]] = []
    for provider_value, count in rows:
        provider_key = provider_value if isinstance(provider_value, str) or provider_value is None else str(provider_value)
        result.append((provider_key, int(count)))
    return result


def simulate_sqlite_counts(
    provider_counts_before: list[tuple[str | None, int]],
    target_provider: str,
    keep_providers: set[str],
) -> list[tuple[str | None, int]]:
    counter: Counter[str | None] = Counter()
    for provider_key, count in provider_counts_before:
        if provider_key is None:
            final_provider = None
        elif provider_key in keep_providers:
            final_provider = provider_key
        else:
            final_provider = target_provider
        counter[final_provider] += count
    return sorted(counter.items(), key=lambda item: (-item[1], '' if item[0] is None else item[0]))


def migrate_sqlite(
    db_path: Path | None,
    target_provider: str,
    keep_providers: set[str],
    apply: bool,
    backup_root: Path | None,
) -> SqliteReport:
    report = SqliteReport(path=db_path)
    if db_path is None or not db_path.exists():
        return report

    connection = sqlite3.connect(db_path)
    try:
        report.provider_counts_before = fetch_sqlite_provider_counts(connection)
        filter_placeholders = ', '.join('?' for _ in keep_providers)
        filter_sql = f'model_provider IS NOT NULL AND model_provider NOT IN ({filter_placeholders})'
        filter_params = tuple(sorted(keep_providers))
        report.rows_needing_update = int(
            connection.execute(f'SELECT COUNT(*) FROM threads WHERE {filter_sql}', filter_params).fetchone()[0]
        )

        if apply and report.rows_needing_update > 0:
            backup_sqlite_bundle(db_path, backup_root)
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                f'UPDATE threads SET model_provider = ? WHERE {filter_sql}',
                (target_provider, *filter_params),
            )
            report.rows_updated = connection.total_changes
            connection.commit()
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')

        report.provider_counts_after = (
            fetch_sqlite_provider_counts(connection)
            if apply
            else simulate_sqlite_counts(report.provider_counts_before, target_provider, keep_providers)
        )
    finally:
        connection.close()

    return report


def sanitize_filename_part(value: str) -> str:
    sanitized = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('.-')
    return sanitized or 'provider'


def mirror_thread_id(source_id: str, target_provider: str) -> str:
    return str(uuid.uuid5(SYNC_NAMESPACE, f'{source_id}:{target_provider}'))


def mirror_rollout_path(source_path: Path, source_id: str, mirror_id: str, target_provider: str) -> Path:
    if source_id in source_path.name:
        return source_path.with_name(source_path.name.replace(source_id, mirror_id, 1))
    provider_part = sanitize_filename_part(target_provider)
    return source_path.with_name(f'{source_path.stem}--{provider_part}-{mirror_id}{source_path.suffix}')


def resolve_sync_targets(
    config_status: ConfigStatus,
    source_provider: str,
    explicit_targets: list[str] | None,
) -> list[str]:
    providers: set[str] = set()
    if explicit_targets:
        providers.update(provider.strip() for provider in explicit_targets if provider.strip())
    else:
        providers.update(resolve_configured_providers(config_status))
    providers.discard(source_provider)
    return sorted(providers)


def resolve_configured_providers(config_status: ConfigStatus) -> list[str]:
    providers = set(config_status.provider_keys)
    if config_status.active_provider:
        providers.add(config_status.active_provider)
    return sorted(provider for provider in providers if provider)


def is_generated_mirror_session(session_meta: dict[str, Any]) -> bool:
    session_id = session_meta.get('id')
    provider = session_meta.get('model_provider')
    forked_from_id = session_meta.get('forked_from_id')
    if not all(isinstance(value, str) and value.strip() for value in (session_id, provider, forked_from_id)):
        return False
    return session_id == mirror_thread_id(forked_from_id, provider)


def find_source_sessions(codex_home: Path, source_provider: str, report: SyncReport) -> list[SourceSession]:
    sessions: list[SourceSession] = []
    for path in iter_rollout_files(codex_home):
        report.files_scanned += 1
        meta_result = get_session_meta(path)
        if meta_result is None:
            continue
        _, session_meta = meta_result
        provider = session_meta.get('model_provider')
        provider_key = provider if isinstance(provider, str) and provider else '<missing>'
        report.provider_counts_before[provider_key] += 1
        report.provider_counts_after[provider_key] += 1
        session_id = session_meta.get('id')
        if provider != source_provider or not isinstance(session_id, str) or not session_id.strip():
            continue
        sessions.append(SourceSession(path=path, thread_id=session_id.strip(), provider=source_provider))
    report.source_sessions_found = len(sessions)
    return sessions


def find_mutual_source_sessions(codex_home: Path, providers: list[str], report: SyncReport) -> list[SourceSession]:
    provider_set = set(providers)
    sessions: list[SourceSession] = []
    for path in iter_rollout_files(codex_home):
        report.files_scanned += 1
        meta_result = get_session_meta(path)
        if meta_result is None:
            continue
        _, session_meta = meta_result
        provider = session_meta.get('model_provider')
        provider_key = provider if isinstance(provider, str) and provider else '<missing>'
        report.provider_counts_before[provider_key] += 1
        report.provider_counts_after[provider_key] += 1
        session_id = session_meta.get('id')
        if (
            not isinstance(provider, str)
            or provider not in provider_set
            or not isinstance(session_id, str)
            or not session_id.strip()
            or is_generated_mirror_session(session_meta)
        ):
            continue
        sessions.append(SourceSession(path=path, thread_id=session_id.strip(), provider=provider))
    report.source_sessions_found = len(sessions)
    return sessions


def build_mirror_plans(source_sessions: list[SourceSession], target_providers: list[str]) -> list[MirrorPlan]:
    plans: list[MirrorPlan] = []
    for session in source_sessions:
        for provider in target_providers:
            mirror_id = mirror_thread_id(session.thread_id, provider)
            plans.append(
                MirrorPlan(
                    source_path=session.path,
                    source_id=session.thread_id,
                    target_provider=provider,
                    mirror_id=mirror_id,
                    mirror_path=mirror_rollout_path(session.path, session.thread_id, mirror_id, provider),
                )
            )
    return plans


def build_mutual_mirror_plans(source_sessions: list[SourceSession], providers: list[str]) -> list[MirrorPlan]:
    plans: list[MirrorPlan] = []
    seen_mirror_ids: set[str] = set()
    for session in source_sessions:
        for provider in providers:
            if provider == session.provider:
                continue
            mirror_id = mirror_thread_id(session.thread_id, provider)
            if mirror_id in seen_mirror_ids:
                continue
            seen_mirror_ids.add(mirror_id)
            plans.append(
                MirrorPlan(
                    source_path=session.path,
                    source_id=session.thread_id,
                    target_provider=provider,
                    mirror_id=mirror_id,
                    mirror_path=mirror_rollout_path(session.path, session.thread_id, mirror_id, provider),
                )
            )
    return plans


def replace_structural_thread_ids(value: Any, source_id: str, mirror_id: str) -> Any:
    id_keys = {'id', 'thread_id', 'session_id', 'parent_thread_id', 'child_thread_id'}
    if isinstance(value, dict):
        replaced: dict[str, Any] = {}
        for key, child in value.items():
            if key in id_keys and child == source_id:
                replaced[key] = mirror_id
            else:
                replaced[key] = replace_structural_thread_ids(child, source_id, mirror_id)
        return replaced
    if isinstance(value, list):
        return [replace_structural_thread_ids(item, source_id, mirror_id) for item in value]
    return value


def render_mirror_jsonl(plan: MirrorPlan) -> str:
    lines, newline_at_end = read_jsonl(plan.source_path)
    rendered_lines: list[str] = []
    session_meta_seen = False

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            rendered_lines.append(line)
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'无法解析 JSONL: {plan.source_path}:{line_number}: {exc}') from exc
        if not isinstance(payload, dict):
            rendered_lines.append(line)
            continue

        payload = replace_structural_thread_ids(payload, plan.source_id, plan.mirror_id)
        if payload.get('type') == 'session_meta':
            session_meta = payload.get('payload')
            if not isinstance(session_meta, dict):
                raise ValueError(f'session_meta payload 不是对象: {plan.source_path}:{line_number}')
            session_meta['id'] = plan.mirror_id
            session_meta['model_provider'] = plan.target_provider
            session_meta.setdefault('forked_from_id', plan.source_id)
            payload['payload'] = session_meta
            session_meta_seen = True
        rendered_lines.append(json_dumps_line(payload))

    if not session_meta_seen:
        raise ValueError(f'未找到 session_meta，无法创建镜像: {plan.source_path}')

    rendered_text = '\n'.join(rendered_lines)
    if newline_at_end:
        rendered_text += '\n'
    return rendered_text


def inspect_existing_mirror_file(plan: MirrorPlan) -> bool:
    meta_result = get_session_meta(plan.mirror_path)
    if meta_result is None:
        return False
    _, session_meta = meta_result
    return session_meta.get('id') == plan.mirror_id and session_meta.get('model_provider') == plan.target_provider


def sync_rollout_mirrors(plans: list[MirrorPlan], apply: bool, report: SyncReport) -> None:
    for plan in plans:
        if plan.mirror_path.exists():
            if inspect_existing_mirror_file(plan):
                report.mirror_files_existing += 1
            else:
                report.mirror_file_conflicts += 1
            continue

        report.mirror_files_needed += 1
        report.provider_counts_after[plan.target_provider] += 1
        if not apply:
            continue

        plan.mirror_path.parent.mkdir(parents=True, exist_ok=True)
        plan.mirror_path.write_text(render_mirror_jsonl(plan), encoding='utf-8')
        report.mirror_files_created += 1


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_thread_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute('PRAGMA table_info(threads)').fetchall()
    return [str(row[1]) for row in rows]


def sqlite_thread_exists(conn: sqlite3.Connection, thread_id: str) -> bool:
    row = conn.execute('SELECT 1 FROM threads WHERE id = ? LIMIT 1', (thread_id,)).fetchone()
    return row is not None


def sqlite_mirror_row_matches(conn: sqlite3.Connection, plan: MirrorPlan) -> bool:
    row = conn.execute(
        'SELECT model_provider, rollout_path FROM threads WHERE id = ?',
        (plan.mirror_id,),
    ).fetchone()
    if row is None:
        return False
    provider, rollout_path = row
    return provider == plan.target_provider and rollout_path == str(plan.mirror_path)


def insert_thread_mirror(conn: sqlite3.Connection, plan: MirrorPlan, columns: list[str]) -> int:
    quoted_columns = ', '.join(quote_identifier(column) for column in columns)
    select_exprs: list[str] = []
    params: list[Any] = []
    for column in columns:
        if column == 'id':
            select_exprs.append('?')
            params.append(plan.mirror_id)
        elif column == 'rollout_path':
            select_exprs.append('?')
            params.append(str(plan.mirror_path))
        elif column == 'model_provider':
            select_exprs.append('?')
            params.append(plan.target_provider)
        else:
            select_exprs.append(quote_identifier(column))
    params.append(plan.source_id)
    before = conn.total_changes
    conn.execute(
        f'INSERT INTO threads ({quoted_columns}) SELECT {", ".join(select_exprs)} FROM threads WHERE id = ?',
        params,
    )
    return conn.total_changes - before


def clone_thread_child_table(conn: sqlite3.Connection, table: str, thread_column: str, plan: MirrorPlan) -> int:
    columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info({quote_identifier(table)})').fetchall()]
    if thread_column not in columns:
        return 0
    quoted_columns = ', '.join(quote_identifier(column) for column in columns)
    select_exprs = ['?' if column == thread_column else quote_identifier(column) for column in columns]
    before = conn.total_changes
    conn.execute(
        f'INSERT OR IGNORE INTO {quote_identifier(table)} ({quoted_columns}) '
        f'SELECT {", ".join(select_exprs)} FROM {quote_identifier(table)} WHERE {quote_identifier(thread_column)} = ?',
        (plan.mirror_id, plan.source_id),
    )
    return conn.total_changes - before


def sync_sqlite_mirrors(
    db_path: Path | None,
    plans: list[MirrorPlan],
    apply: bool,
    backup_root: Path | None,
    report: SyncReport,
) -> SqliteReport:
    sqlite_report = SqliteReport(path=db_path)
    if db_path is None or not db_path.exists():
        report.sqlite_source_rows_missing = len({plan.source_id for plan in plans})
        return sqlite_report

    conn = sqlite3.connect(db_path)
    try:
        sqlite_report.provider_counts_before = fetch_sqlite_provider_counts(conn)
        thread_columns = fetch_thread_columns(conn)
        creatable_plans: list[MirrorPlan] = []

        for plan in plans:
            if not sqlite_thread_exists(conn, plan.source_id):
                report.sqlite_source_rows_missing += 1
                continue
            if sqlite_thread_exists(conn, plan.mirror_id):
                if sqlite_mirror_row_matches(conn, plan):
                    report.sqlite_rows_existing += 1
                else:
                    report.sqlite_rows_conflicting += 1
                continue
            report.sqlite_rows_needed += 1
            creatable_plans.append(plan)

        sqlite_report.rows_needing_update = report.sqlite_rows_needed

        if apply and creatable_plans:
            backup_sqlite_bundle(db_path, backup_root)
            conn.execute('BEGIN IMMEDIATE')
            for plan in creatable_plans:
                report.sqlite_rows_created += insert_thread_mirror(conn, plan, thread_columns)
                clone_thread_child_table(conn, 'thread_dynamic_tools', 'thread_id', plan)
                clone_thread_child_table(conn, 'thread_goals', 'thread_id', plan)
                clone_thread_child_table(conn, 'stage1_outputs', 'thread_id', plan)
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

        sqlite_report.rows_updated = report.sqlite_rows_created
        sqlite_report.provider_counts_after = (
            fetch_sqlite_provider_counts(conn)
            if apply
            else simulate_sync_sqlite_counts(sqlite_report.provider_counts_before, creatable_plans)
        )
    finally:
        conn.close()

    return sqlite_report


def simulate_sync_sqlite_counts(
    provider_counts_before: list[tuple[str | None, int]],
    creatable_plans: list[MirrorPlan],
) -> list[tuple[str | None, int]]:
    counter: Counter[str | None] = Counter()
    for provider_key, count in provider_counts_before:
        counter[provider_key] += count
    for plan in creatable_plans:
        counter[plan.target_provider] += 1
    return sorted(counter.items(), key=lambda item: (-item[1], '' if item[0] is None else item[0]))


def sync_openai_to_all_providers(
    codex_home: Path,
    source_provider: str,
    target_providers: list[str],
    db_path: Path | None,
    apply: bool,
    backup_root: Path | None,
) -> tuple[SyncReport, SqliteReport]:
    report = SyncReport(source_provider=source_provider, target_providers=target_providers)
    source_sessions = find_source_sessions(codex_home, source_provider, report)
    plans = build_mirror_plans(source_sessions, target_providers)
    sync_rollout_mirrors(plans, apply, report)
    sqlite_report = sync_sqlite_mirrors(
        db_path=db_path,
        plans=plans,
        apply=apply,
        backup_root=backup_root,
        report=report,
    )
    return report, sqlite_report


def sync_all_providers_mutually(
    codex_home: Path,
    providers: list[str],
    db_path: Path | None,
    apply: bool,
    backup_root: Path | None,
) -> tuple[SyncReport, SqliteReport]:
    report = SyncReport(source_provider='*', target_providers=providers)
    source_sessions = find_mutual_source_sessions(codex_home, providers, report)
    plans = build_mutual_mirror_plans(source_sessions, providers)
    sync_rollout_mirrors(plans, apply, report)
    sqlite_report = sync_sqlite_mirrors(
        db_path=db_path,
        plans=plans,
        apply=apply,
        backup_root=backup_root,
        report=report,
    )
    return report, sqlite_report


def format_counts(counts: Counter[str] | list[tuple[str | None, int]]) -> list[str]:
    merged: Counter[str] = Counter()
    if isinstance(counts, Counter):
        for provider, count in counts.items():
            label = provider if provider else '<missing>'
            merged[label] += count
    else:
        for provider, count in counts:
            label = provider if isinstance(provider, str) and provider else '<missing>'
            merged[label] += count
    items = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    if not items:
        return ['- none']
    return [f'- {provider}: {count}' for provider, count in items]


def print_config_summary(config_status: ConfigStatus, target_provider: str) -> None:
    print('\n配置检查')
    print(f'- config.toml: {config_status.path}')
    print(f'- 当前 model_provider: {config_status.active_provider or "<missing>"}')
    print(f'- 目标 provider 已定义: {"yes" if config_status.target_defined else "no"}')
    if config_status.provider_keys:
        print(f'- 已定义 providers: {", ".join(config_status.provider_keys)}')
    if config_status.active_provider != target_provider:
        print('- 提醒: config.toml 的 model_provider 与单目标迁移 provider 不一致；迁移历史不会自动修改配置。')
    if not config_status.target_defined:
        print('- 提醒: config.toml 的 model_providers 中未定义单目标迁移 provider；如果它是内置 provider，这通常没问题。')


def print_config_overview(config_status: ConfigStatus) -> None:
    print('\nConfig')
    print(f'- config.toml: {config_status.path}')
    print(f'- active model_provider: {config_status.active_provider or "<missing>"}')
    print(f'- configured providers: {", ".join(config_status.provider_keys) if config_status.provider_keys else "<none>"}')


def print_migration_summary(
    apply: bool,
    codex_home: Path,
    target_provider: str,
    keep_providers: set[str],
    backup_root: Path | None,
    config_status: ConfigStatus,
    rollout_report: RolloutReport,
    sqlite_report: SqliteReport,
) -> None:
    mode = '执行写入' if apply else '预览'
    print(f'模式: 单目标迁移 / {mode}')
    print(f'Codex Home: {codex_home}')
    print(f'目标 provider: {target_provider}')
    print(f'保留 provider: {", ".join(sorted(keep_providers))}')
    if backup_root is not None:
        print(f'备份目录: {backup_root}')

    print_config_summary(config_status, target_provider)

    print('\nrollout 扫描')
    print(f'- 扫描文件数: {rollout_report.files_scanned}')
    print(f'- 需要迁移文件数: {rollout_report.files_needing_update}')
    print(f'- 已改写文件数: {rollout_report.files_updated}')
    print(f'- 改写 session_meta 数: {rollout_report.session_meta_rewritten}')
    print('- 迁移前 provider 分布:')
    for line in format_counts(rollout_report.provider_counts_before):
        print(f'  {line}')
    print('- 迁移后 provider 分布:')
    for line in format_counts(rollout_report.provider_counts_after):
        print(f'  {line}')

    print('\nSQLite 索引')
    print(f'- state db: {sqlite_report.path or "<not found>"}')
    print(f'- 需要更新行数: {sqlite_report.rows_needing_update}')
    print(f'- 已更新行数: {sqlite_report.rows_updated}')
    print('- 迁移前 provider 分布:')
    for line in format_counts(sqlite_report.provider_counts_before):
        print(f'  {line}')
    print('- 迁移后 provider 分布:')
    for line in format_counts(sqlite_report.provider_counts_after):
        print(f'  {line}')

    if not apply:
        print('\n当前为预览模式；追加 --apply 才会真正写入。')


def print_sync_summary(
    apply: bool,
    codex_home: Path,
    backup_root: Path | None,
    config_status: ConfigStatus,
    sync_report: SyncReport,
    sqlite_report: SqliteReport,
) -> None:
    mode = '执行写入' if apply else '预览'
    print(f'模式: openai 会话同步到所有 provider / {mode}')
    print(f'Codex Home: {codex_home}')
    print(f'源 provider: {sync_report.source_provider}')
    print(f'目标 providers: {", ".join(sync_report.target_providers) if sync_report.target_providers else "<none>"}')
    if backup_root is not None:
        print(f'备份目录: {backup_root}')

    print_config_summary(config_status, sync_report.source_provider)

    print('\nrollout 镜像')
    print(f'- 扫描文件数: {sync_report.files_scanned}')
    print(f'- 源会话数: {sync_report.source_sessions_found}')
    print(f'- 需要创建镜像文件数: {sync_report.mirror_files_needed}')
    print(f'- 已创建镜像文件数: {sync_report.mirror_files_created}')
    print(f'- 已存在镜像文件数: {sync_report.mirror_files_existing}')
    print(f'- 文件冲突数: {sync_report.mirror_file_conflicts}')
    print('- 同步前 provider 分布:')
    for line in format_counts(sync_report.provider_counts_before):
        print(f'  {line}')
    print('- 同步后 provider 分布（按 JSONL 预估）:')
    for line in format_counts(sync_report.provider_counts_after):
        print(f'  {line}')

    print('\nSQLite 索引')
    print(f'- state db: {sqlite_report.path or "<not found>"}')
    print(f'- 需要创建镜像行数: {sync_report.sqlite_rows_needed}')
    print(f'- 已创建镜像行数: {sync_report.sqlite_rows_created}')
    print(f'- 已存在镜像行数: {sync_report.sqlite_rows_existing}')
    print(f'- 行冲突数: {sync_report.sqlite_rows_conflicting}')
    print(f'- 缺少源行数: {sync_report.sqlite_source_rows_missing}')
    print('- 同步前 provider 分布:')
    for line in format_counts(sqlite_report.provider_counts_before):
        print(f'  {line}')
    print('- 同步后 provider 分布:')
    for line in format_counts(sqlite_report.provider_counts_after):
        print(f'  {line}')

    if not sync_report.target_providers:
        print('\n未找到可同步的目标 provider；请在 config.toml 的 model_providers 中定义，或传入 --sync-provider。')
    if sync_report.mirror_file_conflicts or sync_report.sqlite_rows_conflicting:
        print('\n存在冲突项，脚本没有覆盖这些记录；请先检查同名镜像文件或相同镜像 id 的 SQLite 行。')
    if not apply:
        print('\n当前为预览模式；追加 --apply 才会真正写入。')


def print_mutual_sync_summary(
    apply: bool,
    codex_home: Path,
    backup_root: Path | None,
    config_status: ConfigStatus,
    sync_report: SyncReport,
    sqlite_report: SqliteReport,
) -> None:
    mode = 'apply' if apply else 'preview'
    print(f'Mode: mutual provider session sync / {mode}')
    print(f'Codex Home: {codex_home}')
    print(f'Providers: {", ".join(sync_report.target_providers) if sync_report.target_providers else "<none>"}')
    if backup_root is not None:
        print(f'Backup dir: {backup_root}')

    print_config_overview(config_status)

    print('\nrollout mirrors')
    print(f'- files scanned: {sync_report.files_scanned}')
    print(f'- source sessions found: {sync_report.source_sessions_found}')
    print(f'- mirror files needed: {sync_report.mirror_files_needed}')
    print(f'- mirror files created: {sync_report.mirror_files_created}')
    print(f'- mirror files existing: {sync_report.mirror_files_existing}')
    print(f'- file conflicts skipped: {sync_report.mirror_file_conflicts}')
    print('- providers before:')
    for line in format_counts(sync_report.provider_counts_before):
        print(f'  {line}')
    print('- providers after estimated by JSONL:')
    for line in format_counts(sync_report.provider_counts_after):
        print(f'  {line}')

    print('\nSQLite index')
    print(f'- state db: {sqlite_report.path or "<not found>"}')
    print(f'- mirror rows needed: {sync_report.sqlite_rows_needed}')
    print(f'- mirror rows created: {sync_report.sqlite_rows_created}')
    print(f'- mirror rows existing: {sync_report.sqlite_rows_existing}')
    print(f'- row conflicts skipped: {sync_report.sqlite_rows_conflicting}')
    print(f'- source rows missing: {sync_report.sqlite_source_rows_missing}')
    print('- providers before:')
    for line in format_counts(sqlite_report.provider_counts_before):
        print(f'  {line}')
    print('- providers after:')
    for line in format_counts(sqlite_report.provider_counts_after):
        print(f'  {line}')

    if not sync_report.target_providers:
        print('\nNo configured providers found. Define model_providers in config.toml first.')
    if sync_report.mirror_file_conflicts or sync_report.sqlite_rows_conflicting:
        print('\nConflicts were skipped. Existing files/rows were not overwritten.')
    if not apply:
        print('\nPreview only. Add --apply to write changes.')


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codex_home = args.codex_home.expanduser().resolve()
    backup_root = ensure_backup_root(args.backup_dir.expanduser().resolve() if args.backup_dir else None)
    config_status = inspect_config(codex_home / 'config.toml', args.target_provider)
    state_db = resolve_state_db(
        codex_home,
        config_status,
        args.state_db.expanduser().resolve() if args.state_db else None,
    )

    if args.sync_all_providers_mutually:
        providers = resolve_configured_providers(config_status)
        sync_report, sqlite_report = sync_all_providers_mutually(
            codex_home=codex_home,
            providers=providers,
            db_path=state_db,
            apply=args.apply,
            backup_root=backup_root,
        )
        print_mutual_sync_summary(
            apply=args.apply,
            codex_home=codex_home,
            backup_root=backup_root,
            config_status=config_status,
            sync_report=sync_report,
            sqlite_report=sqlite_report,
        )
        return 0

    if args.sync_openai_to_all_providers:
        source_provider = args.source_provider.strip()
        if not source_provider:
            raise ValueError('--source-provider 不能为空')
        target_providers = resolve_sync_targets(config_status, source_provider, args.sync_provider)
        sync_report, sqlite_report = sync_openai_to_all_providers(
            codex_home=codex_home,
            source_provider=source_provider,
            target_providers=target_providers,
            db_path=state_db,
            apply=args.apply,
            backup_root=backup_root,
        )
        print_sync_summary(
            apply=args.apply,
            codex_home=codex_home,
            backup_root=backup_root,
            config_status=config_status,
            sync_report=sync_report,
            sqlite_report=sqlite_report,
        )
        return 0

    keep_providers = set(args.keep_provider or ['openai'])
    keep_providers.add(args.target_provider)
    rollout_report = migrate_rollouts(
        codex_home=codex_home,
        target_provider=args.target_provider,
        keep_providers=keep_providers,
        apply=args.apply,
        backup_root=backup_root,
    )
    sqlite_report = migrate_sqlite(
        db_path=state_db,
        target_provider=args.target_provider,
        keep_providers=keep_providers,
        apply=args.apply,
        backup_root=backup_root,
    )
    print_migration_summary(
        apply=args.apply,
        codex_home=codex_home,
        target_provider=args.target_provider,
        keep_providers=keep_providers,
        backup_root=backup_root,
        config_status=config_status,
        rollout_report=rollout_report,
        sqlite_report=sqlite_report,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
