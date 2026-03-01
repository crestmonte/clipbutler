# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for ClipButler Windows build
# Run: pyinstaller build\pa_agent_win.spec

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

block_cipher = None

# Path to bundled ffmpeg.exe (place in build/ before packaging)
FFMPEG_BIN = str(ROOT / 'build' / 'ffmpeg.exe')

a = Analysis(
    [str(ROOT / 'backend' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[
        (FFMPEG_BIN, '.'),
    ],
    datas=[
        (str(ROOT / 'backend'), 'backend'),
        (str(ROOT / 'premiere_panel'), 'premiere_panel'),
        (str(ROOT / 'resolve_integration'), 'resolve_integration'),
        (str(ROOT / 'ui'), 'ui'),
    ],
    hiddenimports=[
        'insightface',
        'onnxruntime',
        'chromadb',
        'whisper',
        'google.generativeai',
        'fastapi',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        'sqlite3',
        'win32api',
        'win32con',
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
    name='ClipButler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClipButler',
)
