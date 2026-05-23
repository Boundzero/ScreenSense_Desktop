# PyInstaller spec for ScreenSense.
# Build with:   pyinstaller ScreenSense.spec --clean
# Output:       dist/ScreenSense.exe  (single-file, ~40-60 MB)
# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Use icon.ico if it exists, otherwise build without one.
ICON_PATH = 'icon.ico' if os.path.exists('icon.ico') else None

# Collect all submodules of the screensense package so PyInstaller bundles
# the whole package and its relative-import structure correctly.
hiddenimports = collect_submodules('screensense')

a = Analysis(
    ['run_screensense.py'],   # standalone entry point (uses absolute imports)
    pathex=['.'],              # current dir contains the screensense/ package
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim PySide6 bulk we don't need — saves ~80 MB.
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='ScreenSense',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
)
