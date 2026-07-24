# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the L2ArbBot single-file executable.

Bundles the stdlib-only launcher (`launcher/run_launcher.py` + the `l2arb`
package) together with a **clean copy of the four component source trees** staged
under `launcher/build/payload/` by `scripts/build_exe.py`. On first run the
frozen exe unpacks that payload into a per-user install dir and builds it.

Build via the driver (handles payload staging first):
    python scripts/build_exe.py
or directly once the payload is staged:
    pyinstaller scripts/l2arbbot.spec --clean --noconfirm
"""

import os

from PyInstaller.building.datastruct import Tree

# SPECPATH is injected by PyInstaller = the dir containing this spec (repo/scripts).
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
LAUNCHER = os.path.join(ROOT, "launcher")
PAYLOAD = os.path.join(LAUNCHER, "build", "payload")

# Bundle the staged component sources under `payload/` inside the exe.
payload_tree = Tree(PAYLOAD, prefix="payload") if os.path.isdir(PAYLOAD) else []

a = Analysis(
    [os.path.join(LAUNCHER, "run_launcher.py")],
    pathex=[LAUNCHER],
    binaries=[],
    datas=[],
    hiddenimports=[
        "l2arb",
        "l2arb.cli",
        "l2arb.installer",
        "l2arb.run",
        "l2arb.proc",
        "l2arb.config",
        "l2arb.prereqs",
        "l2arb.state",
        "l2arb.payload",
        "l2arb.paths",
        "l2arb.console",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "pandas", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    payload_tree,
    [],
    name="L2ArbBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)
