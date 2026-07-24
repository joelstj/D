"""First-run payload extraction for the frozen executable.

The ``.exe`` bundles a clean copy of the four component source trees under
``payload/`` (see ``scripts/l2arbbot.spec``). On first launch we copy them out of
the PyInstaller bundle into the per-user install dir, after which ``install``
builds them in place. A dev checkout ignores this entirely (nothing is frozen).
"""

from __future__ import annotations

import shutil

from . import console
from .paths import COMPONENTS, Layout, bundle_dir, is_frozen


def ensure_payload(lo: Layout) -> None:
    if not is_frozen():
        return
    bd = bundle_dir()
    if not bd:
        return
    src = bd / "payload"
    if not src.exists():
        return
    lo.root.mkdir(parents=True, exist_ok=True)
    copied = []
    for comp in COMPONENTS:
        s = src / comp
        d = lo.root / comp
        if s.exists() and not d.exists():
            shutil.copytree(s, d)
            copied.append(comp)
    if copied:
        console.ok(f"unpacked {', '.join(copied)} → {lo.root}")
