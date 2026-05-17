from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYNC_NAMESPACE = uuid.UUID('7c3bd33f-77a8-4b6f-b91e-6f4236f26b4e')


@dataclass(frozen=True)
class Session:
    path: Path
    session_id: str
    provider: str
    forked_from_id: str | None = None
    line_count: int = 0
    last_timestamp: str | None = None
    size: int = 0
    mtime: float = 0.0


@dataclass(frozen=True)
class MirrorPlan:
    source: Session
    target_provider: str
    mirror_id: str
    mirror_path: Path


@dataclass
class Report:
    files_scanned: int = 0
    provider_counts: Counter[str] | None = None
    source_sessions: int = 0
    mirror_needed: int = 0
    mirror_created: int = 0
    mirror_existing: int = 0
    mirror_stale: int = 0
    mirror_refreshed: int = 0
    mirror_conflicts: int = 0
    migrated_needed: int = 0
    migrated_updated: int = 0
    index_needed: int = 0
    index_added: int = 0
    index_stale: int = 0
    index_updated: int = 0

    def __post_init__(self) -> None:
        if self.provider_counts is None:
            self.provider_counts = Counter()


def default_codex_home() -> Path:
    raw = os.environ.get('CODEX_HOME')
    if raw:
        return Path(raw).expanduser()
    return Path.home() / '.codex'


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Sync Codex Desktop JSONL sessions across model providers for current session_index.jsonl layout.'
    )
    parser.add_argument('--codex-home', type=Path, default=default_codex_home())
    parser.add_argument('--list-providers', action='store_true', help='Only list providers found in session JSONL files.')
    parser.add_argument(
        '--mode',
        choices=('mirror-all', 'mirror', 'migrate'),
        default='mirror-all',
        help='mirror-all creates provider mirrors; mirror copies one provider to target providers; migrate rewrites provider in place.',
    )
    parser.add_argument('--provider', action='append', default=None, help='Provider included in mirror-all. Repeatable.')
    parser.add_argument('--source-provider', default=None, help='Source provider for mirror or migrate mode.')
    parser.add_argument('--target-provider', action='append', default=None, help='Target provider. Repeatable.')
    parser.add_argument('--backup-dir', type=Path, default=None)
    parser.add_argument('--apply', action='store_true', help='Write changes. Default is preview only.')
    parser.add_argument('--no-index', action='store_true', help='Do not update session_index.jsonl for mirror modes.')
    parser.add_argument(
        '--use-symlinks',
        action='store_true',
        help='Rejected for mirror modes because provider mirrors require different file contents.',
    )
    return parser.parse_args(argv)


def iter_session_files(codex_home: Path) -> list[Path]:
    files: list[Path] = []
    for relative in ('sessions', 'archived_sessions'):
        root = codex_home / relative
        if root.exists():
            files.extend(sorted(root.rglob('*.jsonl')))
    return files


def read_jsonl(path: Path) -> tuple[list[str], bool]:
    text = path.read_text(encoding='utf-8-sig')
    return text.splitlines(), text.endswith('\n')


def json_line(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def get_session_meta(path: Path) -> dict[str, Any] | None:
    lines, _ = read_jsonl(path)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid JSONL: {path}:{line_number}: {exc}') from exc
        if isinstance(payload, dict) and payload.get('type') == 'session_meta':
            meta = payload.get('payload')
            if isinstance(meta, dict):
                return meta
            raise ValueError(f'session_meta payload is not an object: {path}:{line_number}')
    return None


def mirror_thread_id(source_id: str, target_provider: str) -> str:
    return str(uuid.uuid5(SYNC_NAMESPACE, f'{source_id}:{target_provider}'))


def is_generated_mirror(meta: dict[str, Any]) -> bool:
    forked_from = meta.get('forked_from_id')
    return isinstance(forked_from, str) and bool(forked_from.strip())


def collect_sessions(codex_home: Path, report: Report) -> list[Session]:
    sessions: list[Session] = []
    for path in iter_session_files(codex_home):
        report.files_scanned += 1
        meta = get_session_meta(path)
        if not meta:
            continue
        provider = meta.get('model_provider')
        provider_key = provider if isinstance(provider, str) and provider.strip() else '<missing>'
        report.provider_counts[provider_key] += 1
        session_id = meta.get('id')
        if not isinstance(provider, str) or not provider.strip():
            continue
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        lines, _ = read_jsonl(path)
        last_timestamp: str | None = None
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = payload.get('timestamp') if isinstance(payload, dict) else None
            if isinstance(timestamp, str) and timestamp.strip():
                last_timestamp = timestamp.strip()
        forked_from_id = meta.get('forked_from_id')
        stat = path.stat()
        sessions.append(
            Session(
                path=path,
                session_id=session_id.strip(),
                provider=provider.strip(),
                forked_from_id=forked_from_id.strip() if isinstance(forked_from_id, str) and forked_from_id.strip() else None,
                line_count=len(lines),
                last_timestamp=last_timestamp,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    return sessions


def sanitize_filename_part(value: str) -> str:
    sanitized = ''.join(char if char.isalnum() or char in '._-' else '-' for char in value).strip('.-')
    return sanitized or 'provider'


def mirror_path(source_path: Path, source_id: str, mirror_id: str, provider: str) -> Path:
    if source_id in source_path.name:
        return source_path.with_name(source_path.name.replace(source_id, mirror_id, 1))
    provider_part = sanitize_filename_part(provider)
    return source_path.with_name(f'{source_path.stem}--{provider_part}-{mirror_id}{source_path.suffix}')


def build_mirror_plans(sessions: list[Session], providers: list[str]) -> list[MirrorPlan]:
    plans: list[MirrorPlan] = []
    seen: set[str] = set()
    provider_set = set(providers)
    for session in sessions:
        if session.provider not in provider_set:
            continue
        for provider in providers:
            if provider == session.provider:
                continue
            mirror_id = mirror_thread_id(session.session_id, provider)
            if mirror_id in seen:
                continue
            seen.add(mirror_id)
            plans.append(
                MirrorPlan(
                    source=session,
                    target_provider=provider,
                    mirror_id=mirror_id,
                    mirror_path=mirror_path(session.path, session.session_id, mirror_id, provider),
                )
            )
    return plans


def lineage_key(session: Session) -> str:
    return session.forked_from_id or session.session_id


def session_rank(session: Session) -> tuple[str, int, int, float]:
    return (session.last_timestamp or '', session.line_count, session.size, session.mtime)


def choose_best_session(sessions: list[Session]) -> Session:
    return max(sessions, key=session_rank)


def choose_provider_sessions(sessions: list[Session]) -> dict[str, Session]:
    selected: dict[str, Session] = {}
    for session in sessions:
        current = selected.get(session.provider)
        if current is None or session_rank(session) > session_rank(current):
            selected[session.provider] = session
    return selected


def build_lineage_mirror_plans(sessions: list[Session], providers: list[str]) -> list[MirrorPlan]:
    provider_set = set(providers)
    groups: dict[str, list[Session]] = {}
    for session in sessions:
        if session.provider not in provider_set:
            continue
        groups.setdefault(lineage_key(session), []).append(session)

    plans: list[MirrorPlan] = []
    seen_targets: set[tuple[Path, str]] = set()
    for root_id, group_sessions in groups.items():
        provider_sessions = choose_provider_sessions(group_sessions)
        if not provider_sessions:
            continue
        canonical = choose_best_session(list(provider_sessions.values()))
        root_session = next((session for session in group_sessions if session.session_id == root_id), canonical)

        for provider in providers:
            target_session = provider_sessions.get(provider)
            if target_session is not None:
                if target_session.path == canonical.path:
                    continue
                mirror_id = target_session.session_id
                target_path = target_session.path
            else:
                mirror_id = mirror_thread_id(root_id, provider)
                source_id_for_path = root_id if root_id in root_session.path.name else root_session.session_id
                target_path = mirror_path(root_session.path, source_id_for_path, mirror_id, provider)

            target_key = (target_path, mirror_id)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            plans.append(
                MirrorPlan(
                    source=canonical,
                    target_provider=provider,
                    mirror_id=mirror_id,
                    mirror_path=target_path,
                )
            )
    return plans


def replace_thread_ids(value: Any, source_id: str, mirror_id: str) -> Any:
    id_keys = {'id', 'thread_id', 'session_id', 'parent_thread_id', 'child_thread_id'}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            result[key] = mirror_id if key in id_keys and child == source_id else replace_thread_ids(child, source_id, mirror_id)
        return result
    if isinstance(value, list):
        return [replace_thread_ids(item, source_id, mirror_id) for item in value]
    return value


def render_mirror(plan: MirrorPlan) -> str:
    lines, newline_at_end = read_jsonl(plan.source.path)
    rendered: list[str] = []
    meta_seen = False
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            rendered.append(line)
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            rendered.append(line)
            continue
        payload = replace_thread_ids(payload, plan.source.session_id, plan.mirror_id)
        if payload.get('type') == 'session_meta':
            meta = payload.get('payload')
            if not isinstance(meta, dict):
                raise ValueError(f'session_meta payload is not an object: {plan.source.path}:{line_number}')
            meta['id'] = plan.mirror_id
            meta['model_provider'] = plan.target_provider
            meta.setdefault('forked_from_id', plan.source.session_id)
            payload['payload'] = meta
            meta_seen = True
        rendered.append(json_line(payload))
    if not meta_seen:
        raise ValueError(f'session_meta not found: {plan.source.path}')
    text = '\n'.join(rendered)
    if newline_at_end:
        text += '\n'
    return text


def backup_file(path: Path, codex_home: Path, backup_dir: Path | None) -> None:
    if backup_dir is None:
        return
    if not path.exists():
        return
    try:
        relative = path.relative_to(codex_home)
    except ValueError:
        relative = Path(path.name)
    destination = backup_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(path, destination)


def inspect_existing_mirror(plan: MirrorPlan) -> bool:
    meta = get_session_meta(plan.mirror_path)
    if not meta:
        return False
    return meta.get('id') == plan.mirror_id and meta.get('model_provider') == plan.target_provider


def apply_mirrors(plans: list[MirrorPlan], codex_home: Path, backup_dir: Path | None, apply: bool, report: Report) -> None:
    for plan in plans:
        if plan.mirror_path.exists():
            if not inspect_existing_mirror(plan):
                report.mirror_conflicts += 1
                continue
            rendered = render_mirror(plan)
            current = plan.mirror_path.read_text(encoding='utf-8-sig')
            if current == rendered:
                report.mirror_existing += 1
                continue
            report.mirror_stale += 1
            if apply:
                backup_file(plan.mirror_path, codex_home, backup_dir)
                plan.mirror_path.write_text(rendered, encoding='utf-8')
                report.mirror_refreshed += 1
            continue
        report.mirror_needed += 1
        if not apply:
            continue
        plan.mirror_path.parent.mkdir(parents=True, exist_ok=True)
        plan.mirror_path.write_text(render_mirror(plan), encoding='utf-8')
        report.mirror_created += 1


def load_index(index_path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not index_path.exists():
        return [], {}
    items: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(index_path.read_text(encoding='utf-8-sig').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid session index: {index_path}:{line_number}: {exc}') from exc
        if not isinstance(item, dict):
            continue
        items.append(item)
        item_id = item.get('id')
        if isinstance(item_id, str):
            by_id[item_id] = item
    return items, by_id


def fallback_index_item(plan: MirrorPlan) -> dict[str, Any]:
    updated_at = datetime.fromtimestamp(plan.source.path.stat().st_mtime, timezone.utc).isoformat().replace('+00:00', 'Z')
    return {
        'id': plan.mirror_id,
        'thread_name': plan.source.path.stem,
        'updated_at': updated_at,
    }


def update_session_index(plans: list[MirrorPlan], codex_home: Path, backup_dir: Path | None, apply: bool, report: Report) -> None:
    index_path = codex_home / 'session_index.jsonl'
    items, by_id = load_index(index_path)
    additions: list[dict[str, Any]] = []
    changed = False
    for plan in plans:
        source_item = by_id.get(plan.source.session_id)
        item = dict(source_item) if source_item else fallback_index_item(plan)
        item['id'] = plan.mirror_id
        existing = by_id.get(plan.mirror_id)
        if existing is None:
            additions.append(item)
            continue
        if existing == item:
            continue
        report.index_stale += 1
        if apply:
            existing.clear()
            existing.update(item)
            changed = True
    report.index_needed = len(additions)
    if not apply or (not additions and not changed):
        return
    backup_file(index_path, codex_home, backup_dir)
    text = '\n'.join(json_line(item) for item in [*items, *additions])
    if text:
        text += '\n'
    index_path.write_text(text, encoding='utf-8')
    report.index_added = len(additions)
    report.index_updated = report.index_stale


def migrate_provider(
    sessions: list[Session],
    source_provider: str,
    target_provider: str,
    codex_home: Path,
    backup_dir: Path | None,
    apply: bool,
    report: Report,
) -> None:
    for session in sessions:
        if session.provider != source_provider:
            continue
        report.migrated_needed += 1
        if not apply:
            continue
        lines, newline_at_end = read_jsonl(session.path)
        rendered: list[str] = []
        for line in lines:
            if not line.strip():
                rendered.append(line)
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get('type') == 'session_meta':
                meta = payload.get('payload')
                if isinstance(meta, dict):
                    meta['model_provider'] = target_provider
                    payload['payload'] = meta
                    line = json_line(payload)
            rendered.append(line)
        backup_file(session.path, codex_home, backup_dir)
        text = '\n'.join(rendered)
        if newline_at_end:
            text += '\n'
        session.path.write_text(text, encoding='utf-8')
        report.migrated_updated += 1


def print_provider_counts(report: Report) -> None:
    print('Providers found in JSONL:')
    for provider, count in sorted(report.provider_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f'- {provider}: {count}')


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codex_home = args.codex_home.expanduser().resolve()
    backup_dir = args.backup_dir.expanduser().resolve() if args.backup_dir else None
    if backup_dir and args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)

    report = Report()
    sessions = collect_sessions(codex_home, report)
    if args.list_providers:
        print_provider_counts(report)
        return 0

    if args.use_symlinks and args.mode != 'migrate':
        raise SystemExit('Symlinks cannot be used for provider mirrors because mirror files need different ids and model_provider values.')

    mode = 'apply' if args.apply else 'preview'
    print(f'Mode: {args.mode} / {mode}')
    print(f'Codex Home: {codex_home}')
    if backup_dir:
        print(f'Backup dir: {backup_dir}')
    print_provider_counts(report)

    if args.mode == 'migrate':
        if not args.source_provider or not args.target_provider or len(args.target_provider) != 1:
            raise SystemExit('migrate mode requires --source-provider and exactly one --target-provider.')
        migrate_provider(
            sessions=sessions,
            source_provider=args.source_provider,
            target_provider=args.target_provider[0],
            codex_home=codex_home,
            backup_dir=backup_dir,
            apply=args.apply,
            report=report,
        )
        print('\nMigration:')
        print(f'- source provider: {args.source_provider}')
        print(f'- target provider: {args.target_provider[0]}')
        print(f'- files needing update: {report.migrated_needed}')
        print(f'- files updated: {report.migrated_updated}')
        if not args.apply:
            print('\nPreview only. Add --apply to write changes.')
        return 0

    if args.mode == 'mirror':
        if not args.source_provider or not args.target_provider:
            raise SystemExit('mirror mode requires --source-provider and at least one --target-provider.')
        selected_sessions = [session for session in sessions if session.provider == args.source_provider]
        providers = [args.source_provider, *args.target_provider]
        plans = build_mirror_plans(selected_sessions, providers)
    else:
        providers = args.provider or [provider for provider in report.provider_counts if provider != '<missing>']
        plans = build_lineage_mirror_plans(sessions, providers)

    report.source_sessions = len({plan.source.session_id for plan in plans})
    apply_mirrors(plans, codex_home, backup_dir, args.apply, report)
    if not args.no_index:
        update_session_index(plans, codex_home, backup_dir, args.apply, report)

    print('\nMirrors:')
    print(f'- providers: {", ".join(providers) if providers else "<none>"}')
    print(f'- source sessions: {report.source_sessions}')
    print(f'- mirror files needed: {report.mirror_needed}')
    print(f'- mirror files created: {report.mirror_created}')
    print(f'- mirror files existing: {report.mirror_existing}')
    print(f'- mirror files stale: {report.mirror_stale}')
    print(f'- mirror files refreshed: {report.mirror_refreshed}')
    print(f'- mirror file conflicts: {report.mirror_conflicts}')
    print(f'- session_index entries needed: {report.index_needed}')
    print(f'- session_index entries added: {report.index_added}')
    print(f'- session_index entries stale: {report.index_stale}')
    print(f'- session_index entries updated: {report.index_updated}')
    if not args.apply:
        print('\nPreview only. Add --apply to write changes.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
