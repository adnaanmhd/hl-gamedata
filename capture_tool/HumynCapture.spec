# PyInstaller spec for HumynCapture.
#
# MUST be run on Windows with Windows Python — PyInstaller does not
# cross-compile. Running `pyinstaller` on macOS/Linux against this spec
# produces a macOS/Linux binary, not a .exe.
#
# Usage (from this directory, on Windows, inside the venv described in
# README.md "Building the .exe"):
#     pyinstaller HumynCapture.spec
#
# Output: dist/HumynCapture/HumynCapture.exe (onedir build — matches the
# original bundle's layout of exe + loose DLLs/pyc next to it, easier to
# debug than --onefile if something's missing at runtime).

import sys
from pathlib import Path

block_cipher = None

# repo root (one level up) must be on the path so `import translator` resolves
# — the finalize pipeline (app/core/finalize/pipeline.py) imports it directly
# rather than vendoring a second copy. See README.md "Design deviations".
REPO_ROOT = str(Path(SPECPATH).parent.parent)

a = Analysis(
    ["app/main.py"],
    pathex=[SPECPATH, REPO_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # translator/ (repo root) — used by app.core.finalize.pipeline
        "translator",
        "translator.v2",
        "translator.binner",
        "translator.trim",
        "translator.sync",
        "translator.rrd",
        "translator.video",
        "translator.keys",
        "translator.keybind",
        "translator.keybinds",
        "translator.translate",
        # pynput's Windows backends aren't always picked up by static
        # analysis since they're selected at import time by platform.
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HumynCapture",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console window, matches the original
    disable_windowed_traceback=False,
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
    upx=False,
    upx_exclude=[],
    name="HumynCapture",
)
