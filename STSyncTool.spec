# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ST SyncTool — macOS .app bundle (M7.1).

Builds an unsigned ``ST SyncTool.app``. Code-signing + notarization are applied
afterward by the release runbook (docs/release.md) once an Apple Developer ID
is available; this spec deliberately leaves ``codesign_identity`` unset so the
unsigned build works for local testing.

Submodules under core/, gui/ and utils/ are collected automatically so new
modules never need to be added by hand. rclone is bundled into the app when it
is found on the build machine's PATH, giving testers a no-terminal install
(ffmpeg/ffprobe stay optional and PATH-based — contact sheets degrade
gracefully without them; see docs/release.md).
"""

import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# Single source of truth for the version (core/version.py).
_ns = {}
exec(Path("core/version.py").read_text(), _ns)
APP_VERSION = _ns["__version__"]

hiddenimports = (
    collect_submodules("core")
    + collect_submodules("gui")
    + collect_submodules("utils")
    + ["PyQt6", "xxhash", "humanize", "pyperclip", "requests", "PIL"]
)

# Bundle rclone if present on the build machine so the app is self-contained.
binaries = []
_rclone = shutil.which("rclone")
if _rclone:
    binaries.append((_rclone, "."))

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=binaries,
    datas=[('assets/app_icon.png', 'assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='STSyncTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX corrupts macOS binaries and breaks code-signing.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,     # host arch; set 'universal2' for a fat release build.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='STSyncTool',
)

app = BUNDLE(
    coll,
    name='ST SyncTool.app',
    icon='assets/app_icon.icns',   # brand mark (regenerate via scripts/gen_app_icon.py)
    bundle_identifier='com.signaltheory.stsynctool',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'CFBundleDisplayName': 'ST SyncTool',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'Signal Theory',
    },
)
