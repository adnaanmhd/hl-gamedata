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

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# rerun-sdk is a hybrid Python+Rust package (native .pyd extension modules
# plus bundled data/asset files for its viewer). `import rerun` succeeding
# in a plain venv does NOT guarantee PyInstaller's static import analysis
# catches everything it needs — this is a common "works unfrozen, breaks
# frozen" gap for packages with compiled extensions. collect_all() pulls in
# rerun's binaries/datas/submodules explicitly instead of hoping Analysis's
# static scan finds them all on its own. Needed for app.core.finalize.
# pipeline's in_process rrd generation (see translator/rrd.py).
rerun_datas, rerun_binaries, rerun_hiddenimports = collect_all("rerun")

# repo root must be on the path so `import translator` resolves — the
# finalize pipeline (app/core/finalize/pipeline.py) imports it directly
# rather than vendoring a second copy. See README.md "Design deviations".
#
# SPECPATH is ALREADY the directory containing this .spec file (PyInstaller
# sets it that way, it is not the file's own path) — i.e. .../hl-gamedata/
# capture_tool. One .parent gets the repo root (.../hl-gamedata, where
# translator/ lives). Real bug found on Windows: this used to be
# `.parent.parent`, going one directory too far up past the repo root, so
# translator/ was never on PyInstaller's analysis path, was never bundled,
# and the frozen exe raised `ImportError: no module named 'translator'` at
# finalize time — every session failed at "Writing delivery files...".
REPO_ROOT = str(Path(SPECPATH).parent)

a = Analysis(
    ["app/main.py"],
    pathex=[SPECPATH, REPO_ROOT],
    binaries=rerun_binaries,
    datas=rerun_datas,
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
        *rerun_hiddenimports,
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
