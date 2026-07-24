"""Unit tests for :class:`l2arb.model.blockstamp.Blockstamp`."""

from __future__ import annotations

import pytest

from l2arb.errors import DataError
from l2arb.model.blockstamp import Blockstamp

pytestmark = pytest.mark.unit

HASH = "0x" + "ab" * 32


def _stamp(**over: object) -> Blockstamp:
    kw: dict[str, object] = {
        "chain_id": 42161,
        "number": 100,
        "block_hash": HASH,
        "timestamp": 1_700_000_000,
    }
    kw.update(over)
    return Blockstamp(**kw)  # type: ignore[arg-type]


def test_construct_and_fields() -> None:
    bs = _stamp()
    assert bs.chain_id == 42161
    assert bs.number == 100
    assert bs.timestamp == 1_700_000_000


def test_hash_is_lowercased() -> None:
    bs = _stamp(block_hash="0x" + "AB" * 32)
    assert bs.block_hash == HASH


def test_frozen_and_hashable() -> None:
    bs = _stamp()
    assert {bs: 1}[bs] == 1
    with pytest.raises((AttributeError, Exception)):
        bs.number = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", 0),
        ("chain_id", -1),
        ("number", -1),
        ("timestamp", -1),
        ("block_hash", "0x1234"),
        ("block_hash", "deadbeef"),
        ("block_hash", "0x" + "zz" * 32),
    ],
)
def test_invalid_inputs_raise_data_error(field: str, value: object) -> None:
    with pytest.raises(DataError):
        _stamp(**{field: value})


def test_zero_block_number_is_allowed() -> None:
    # Genesis (block 0) is valid; only negatives are rejected.
    assert _stamp(number=0).number == 0


def test_age_seconds_and_staleness() -> None:
    bs = _stamp(timestamp=1000)
    assert bs.age_seconds(1005) == 5
    assert bs.age_seconds(1000) == 0
    # Clock skew: block slightly ahead of local clock clamps to 0, not negative.
    assert bs.age_seconds(995) == 0
    assert bs.is_stale(1006, max_age_s=5) is True
    assert bs.is_stale(1005, max_age_s=5) is False


def test_is_stale_rejects_negative_bound() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _stamp().is_stale(2000, max_age_s=-1)


def test_ordering_same_chain() -> None:
    older = _stamp(number=100)
    newer = _stamp(number=200)
    assert newer.is_same_or_newer(older) is True
    assert older.is_same_or_newer(newer) is False
    assert newer.is_same_or_newer(newer) is True


def test_ordering_across_chains_is_a_bug() -> None:
    a = _stamp(chain_id=42161, number=1)
    b = _stamp(chain_id=8453, number=1)
    with pytest.raises(ValueError, match="across chains"):
        a.is_same_or_newer(b)
