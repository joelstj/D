"""l2arb — off-chain, near-zero-latency arbitrage *detection* for Layer 2 chains.

Detection only: this package reads public on-chain state and emits arbitrage
opportunities. It holds no keys, signs nothing, and submits no transactions.
See CLAUDE.md §1 and docs/SECURITY.md for the scope invariants.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.1"
