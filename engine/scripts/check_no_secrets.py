#!/usr/bin/env python3
"""Block accidental commits of private keys / secrets.

Enforces docs/SECURITY.md sections 1-2. Scans staged files (or argv paths) for
credential shapes. Pure stdlib; runs in pre-commit and CI.

Design note — avoiding false positives on legitimate on-chain data:
A raw ``0x`` + 64 hex characters is the shape of BOTH an Ethereum private key AND
a block hash / transaction hash. Block and tx hashes appear all over this project
as legitimate fixture data, so we do NOT flag bare 64-hex values. Instead a
64-hex value is flagged only when its line also carries key-indicative context
(``private``, ``secret``, ``mnemonic``, ``privkey``, ``PRIVATE KEY`` ...). We also
flag PEM private-key blocks, BIP39-style mnemonic assignments, and common cloud
API-key shapes outright. This keeps the check strict about secrets without
tripping over verifiable on-chain hashes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HEX64 = re.compile(r"0x[0-9a-fA-F]{64}\b")
KEY_CONTEXT = re.compile(r"(?i)(private[_-]?key|privkey|secret|mnemonic|seed[_-]?phrase)")
PEM_PRIVATE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")
AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
GENERIC_ASSIGN_SECRET = re.compile(
    r"(?i)(private_key|privkey|mnemonic|seed[_-]?phrase)\s*[:=]\s*(['\"])[^'\"\n]{6,}\2"
)

# Files/dirs never scanned. These legitimately contain secret-SHAPED strings:
# this script documents the detection patterns, the example env is placeholders,
# and the guard's own test pins its behaviour with fixture secrets.
SKIP_SUBSTRINGS = (
    "scripts/check_no_secrets.py",
    "tests/test_check_no_secrets.py",
    ".env.example",
    "/.git/",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    reason: str


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    """Return credential findings in ``text`` (context-aware; see module docs)."""
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if PEM_PRIVATE.search(line):
            findings.append(Finding(path, i, "PEM private key block"))
        if AWS_ACCESS_KEY.search(line):
            findings.append(Finding(path, i, "AWS access key id"))
        if GENERIC_ASSIGN_SECRET.search(line):
            findings.append(Finding(path, i, "assignment to a secret-named field"))
        if HEX64.search(line) and KEY_CONTEXT.search(line):
            findings.append(Finding(path, i, "64-hex value in key context (possible private key)"))
    return findings


def _should_skip(path: str) -> bool:
    return any(s in path for s in SKIP_SUBSTRINGS)


def scan_files(paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for p in paths:
        if _should_skip(p):
            continue
        fp = Path(p)
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(text, p))
    return findings


def _staged_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    paths = argv[1:] if len(argv) > 1 else _staged_files()
    findings = scan_files(paths)
    if findings:
        print("Potential secrets detected — commit blocked:")
        for f in findings:
            print(f"  - {f.path}:{f.line_no}: {f.reason}")
        return 1
    print("no-secrets: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
