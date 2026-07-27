"""Blockstamp — the provenance tag carried by every piece of on-chain state.

Data-integrity rule (CLAUDE.md §3): *a detection the engine cannot tie back to a
specific block is a bug.* Every reserve, price, and derived quote therefore
carries a :class:`Blockstamp` naming the exact chain, block number, block hash,
and timestamp it was read at. The block number drives the caches' monotonic-apply
ordering (drop older updates; accept a same-or-newer block, including a
same-height reorg replacement — :meth:`is_same_or_newer`); the timestamp drives
wall-clock freshness (:meth:`is_stale`, ``PoolStateCache.evict_stale``); the block
hash is retained for provenance and replay.

Units:
    * ``chain_id``  — EIP-155 chain id (Arbitrum One = 42161, Base = 8453, …).
    * ``number``    — block height (non-negative integer).
    * ``block_hash``— 0x-prefixed 32-byte hex string, lower-cased.
    * ``timestamp`` — block timestamp in **whole unix seconds** (UTC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from l2arb.errors import DataError

__all__ = ["Blockstamp"]

_HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True, order=False)
class Blockstamp:
    """Immutable (chain, block) provenance stamp.

    Frozen + slotted for a small, hashable, allocation-cheap value object — these
    are created on every state update on the hot path, so they must stay light.
    """

    chain_id: int
    number: int
    block_hash: str
    timestamp: int

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise DataError(f"chain_id must be positive, got {self.chain_id}")
        if self.number < 0:
            raise DataError(f"block number must be non-negative, got {self.number}")
        if self.timestamp < 0:
            raise DataError(f"timestamp must be non-negative, got {self.timestamp}")
        # Normalise then validate the hash so equality/hashing are canonical.
        normalised = self.block_hash.lower()
        if not _HASH_RE.match(normalised):
            raise DataError(f"block_hash must be 0x + 64 hex chars, got {self.block_hash!r}")
        if normalised != self.block_hash:
            object.__setattr__(self, "block_hash", normalised)

    def age_seconds(self, now_ts: int) -> int:
        """Age of this block relative to ``now_ts`` (unix seconds).

        Clamped at zero: a block whose timestamp is (slightly) ahead of the
        local clock — common with fast L2s and clock skew — reads as age 0, not
        a negative age.
        """
        return max(0, now_ts - self.timestamp)

    def is_stale(self, now_ts: int, max_age_s: int) -> bool:
        """True iff this block is older than ``max_age_s`` seconds at ``now_ts``."""
        if max_age_s < 0:
            raise ValueError("max_age_s must be non-negative")
        return self.age_seconds(now_ts) > max_age_s

    def is_same_or_newer(self, other: Blockstamp) -> bool:
        """True iff ``self`` is at least as new as ``other`` on the same chain.

        Used to reject out-of-order updates on the event path (a late log must
        never overwrite fresher reserves). Comparing stamps from different chains
        is a programming error, not a data error.
        """
        if self.chain_id != other.chain_id:
            raise ValueError(
                f"cannot order stamps across chains {self.chain_id} vs {other.chain_id}"
            )
        return self.number >= other.number
