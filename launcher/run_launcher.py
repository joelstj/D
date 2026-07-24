"""PyInstaller entry point for the L2ArbBot executable.

Kept as a thin, top-level script so PyInstaller has a simple entry module. All
logic lives in the ``l2arb`` package (bundled alongside). Running this file
directly also works in a dev checkout, since ``launcher/`` is on ``sys.path``.
"""

from __future__ import annotations

from l2arb.cli import _entrypoint

if __name__ == "__main__":
    _entrypoint()
