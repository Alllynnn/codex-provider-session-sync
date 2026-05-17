from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_PROVIDERS = ('openai', 'openrouter', 'custom')
MIN_INTERVAL_SECONDS = 30


@dataclass(frozen=True)
class SyncSettings:
    enabled: bool = True
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    providers: tuple[str, ...] = DEFAULT_PROVIDERS


def default_codex_home() -> Path:
    raw = os.environ.get('CODEX_HOME')
    if raw:
        return Path(raw).expanduser()
    return Path.home() / '.codex'


def settings_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / 'provider-session-sync.json'


def normalize_settings(data: dict[str, Any]) -> SyncSettings:
    interval = data.get('interval_seconds', DEFAULT_INTERVAL_SECONDS)
    if not isinstance(interval, int):
        interval = DEFAULT_INTERVAL_SECONDS
    interval = max(MIN_INTERVAL_SECONDS, interval)

    providers = data.get('providers', DEFAULT_PROVIDERS)
    if isinstance(providers, list):
        provider_values = tuple(str(item).strip() for item in providers if str(item).strip())
    else:
        provider_values = DEFAULT_PROVIDERS
    if not provider_values:
        provider_values = DEFAULT_PROVIDERS

    return SyncSettings(
        enabled=bool(data.get('enabled', True)),
        interval_seconds=interval,
        providers=provider_values,
    )


def load_settings(codex_home: Path | None = None) -> SyncSettings:
    path = settings_path(codex_home)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return SyncSettings()
    if not isinstance(payload, dict):
        return SyncSettings()
    return normalize_settings(payload)


def save_settings(settings: SyncSettings, codex_home: Path | None = None) -> None:
    path = settings_path(codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'enabled': settings.enabled,
        'interval_seconds': settings.interval_seconds,
        'providers': list(settings.providers),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def update_settings(codex_home: Path | None = None, **changes: Any) -> SyncSettings:
    current = load_settings(codex_home)
    merged = {
        'enabled': current.enabled,
        'interval_seconds': current.interval_seconds,
        'providers': list(current.providers),
    }
    merged.update(changes)
    settings = normalize_settings(merged)
    save_settings(settings, codex_home)
    return settings
