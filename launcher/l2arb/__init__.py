"""L2 Arbitrage Flash-Loan Bot — launcher / installer / supervisor.

A stdlib-only orchestrator that installs and runs the merged bot's three runnable
components (Python detection engine, Rust ingestion, Node dashboard) and serves
the dashboard UI. It doubles as the payload wrapped by the Windows ``.exe``.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
