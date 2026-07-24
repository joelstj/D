"""Tests for the no-secrets guard (scripts/check_no_secrets.py).

Notably pins the deliberate design choice that legitimate on-chain hashes (block
hashes, tx hashes — ``0x`` + 64 hex) are NOT flagged, while the same shape in a
key context IS. See the module docstring of check_no_secrets.
"""

from __future__ import annotations

import pytest
from check_no_secrets import scan_text

pytestmark = pytest.mark.unit

# A real-shaped block hash — must NOT be flagged (verifiable on-chain data).
BLOCK_HASH_LINE = 'BLOCK_HASH = "0x' + "ab" * 32 + '"  # arbitrum block 123'
# The same 64-hex shape, but in a private-key context — MUST be flagged.
PRIVATE_KEY_LINE = 'private_key = "0x' + "cd" * 32 + '"'


def test_block_hash_not_flagged() -> None:
    assert scan_text(BLOCK_HASH_LINE) == []


def test_private_key_in_context_flagged() -> None:
    findings = scan_text(PRIVATE_KEY_LINE)
    assert len(findings) >= 1


def test_pem_private_key_flagged() -> None:
    findings = scan_text("-----BEGIN PRIVATE KEY-----")
    assert any("PEM" in f.reason for f in findings)


def test_mnemonic_assignment_flagged() -> None:
    findings = scan_text('mnemonic = "test test test test junk junk junk"')
    assert findings


def test_aws_access_key_flagged() -> None:
    findings = scan_text("aws_key = AKIAIOSFODNN7EXAMPLE")
    assert any("AWS" in f.reason for f in findings)


def test_clean_line_not_flagged() -> None:
    assert scan_text("reserves = pool.get_reserves()  # read-only") == []
