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
        if not s.exists() or d.exists():
            continue
        try:
            shutil.copytree(s, d)
        except FileExistsError:
            # Double-clicking the .exe twice (e.g. "nothing happened, click
            # again") starts two processes racing the same `not d.exists()`
            # check above. `copytree` creates `d` itself as its very first
            # step, so the loser fails atomically here, before copying a single
            # file — nothing partial was written. The other instance already
            # is (or will shortly be) unpacking this component, so this is not
            # a real failure; without this handler it was an uncaught
            # FileExistsError propagating out of `main()` (payload unpack runs
            # before `main()`'s own try/except), correctly caught by
            # `cli._run_main_safely`'s crash net but a needless scary
            # traceback for something that isn't actually broken.
            continue
        copied.append(comp)
    if copied:
        console.ok(f"unpacked {', '.join(copied)} → {lo.root}")
