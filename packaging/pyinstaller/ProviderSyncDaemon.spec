# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


REPO_ROOT = Path(SPECPATH).parents[1]

a = Analysis(
    [str(REPO_ROOT / 'src' / 'provider_sync_daemon.py')],
    pathex=[str(REPO_ROOT / 'src')],
    binaries=[],
    datas=[],
    hiddenimports=['provider_sync_v2', 'provider_sync_settings'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ProviderSyncDaemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(REPO_ROOT / 'assets' / 'provider-sync.ico')],
)
