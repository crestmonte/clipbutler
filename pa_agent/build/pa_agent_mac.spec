# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for ClipButler macOS build
# Run from pa_agent/: pyinstaller build/pa_agent_mac.spec

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

block_cipher = None

a = Analysis(
    [str(ROOT / 'run.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'backend'), 'backend'),
        (str(ROOT / 'ui'), 'ui'),
    ],
    hiddenimports=[
        # ChromaDB
        'chromadb',
        'chromadb.api',
        'chromadb.api.client',
        'chromadb.db.impl',
        'chromadb.db.impl.sqlite',
        # FastAPI / uvicorn
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
        # Standard lib
        'sqlite3',
        'email.mime.multipart',
        'email.mime.text',
        # HTTP client
        'requests',
        # Optional face / transcription — import-guarded in code
        # 'insightface', 'onnxruntime', 'whisper',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'notebook', 'IPython'],
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
    name='CLPBTLR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # no terminal window — service runs headless
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity="Developer ID Application: Crestmonte Media LLC (K6W4HB67DJ)",
    entitlements_file=str(ROOT / 'build' / 'entitlements.plist'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CLPBTLR',
)

app = BUNDLE(
    coll,
    name='CLPBTLR.app',
    icon=str(ROOT / 'build' / 'CLPBTLR.icns'),
    bundle_identifier='com.clpbtlr.app',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSBackgroundOnly': True,   # background-only service; no Dock icon
        'LSUIElement': True,
    },
)
