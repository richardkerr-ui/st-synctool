# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ST SyncTool — macOS .app bundle."""

from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'xxhash',
        'humanize',
        'pyperclip',
        'requests',
        'core',
        'core.checksum',
        'core.manifest',
        'core.comparison',
        'core.transfer',
        'core.rclone_bridge',
        'core.merge_ops',
        'core.amphetamine',
        'core.offload',
        'core.preflight',
        'core.projects',
        'core.oauth_config',
        'core.setup_checks',
        'core.thumbnail',
        'core.demo',
        'gui',
        'gui.main_window',
        'gui.transfer_tab',
        'gui.merge_tab',
        'gui.verify_tab',
        'gui.log_widget',
        'gui.path_input_widget',
        'gui.diff_table',
        'gui.theme',
        'utils',
        'utils.file_utils',
        'utils.gdrive_utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='STSyncTool',
)

app = BUNDLE(
    coll,
    name='ST SyncTool.app',
    icon=None,
    bundle_identifier='com.signaltheory.stsynctool',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'CFBundleDisplayName': 'ST SyncTool',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': 'Signal Theory',
    },
)
