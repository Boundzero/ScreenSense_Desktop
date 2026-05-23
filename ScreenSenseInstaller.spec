# PyInstaller spec for ScreenSense Installer.
# Build with:   pyinstaller ScreenSenseInstaller.spec --clean
#
# IMPORTANT: Build the main app first so dist/ScreenSense.exe exists,
# or use build_all.py which orchestrates both builds.
# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

ICON_PATH = 'icon.ico' if os.path.exists('icon.ico') else None

hiddenimports = collect_submodules('installer')

# Bundle the main app and autorun files as payload data.
payload_files = [
    ('dist/ScreenSense.exe', 'payload'),
    ('autorun.inf', 'payload'),
    ('Install-Autorun.bat', 'payload'),
]
# Filter to only files that exist — gives a clearer error if main app wasn't built
payload_files = [(src, dst) for src, dst in payload_files if os.path.exists(src)]

a = Analysis(
    ['run_installer.py'],
    pathex=['.'],
    binaries=[],
    datas=payload_files,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'tkinter',
        # The installer doesn't need OCR or screen capture or the LLM client
        'rapidocr_onnxruntime', 'mss', 'imagehash', 'pynput', 'anthropic',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='ScreenSenseInstaller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3.dll'],
    runtime_tmpdir=None,
    console=False,
    icon=ICON_PATH,
)
