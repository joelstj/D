#!/usr/bin/env python3
"""Fail if any runtime module lacks a paired test module.

This mechanically enforces the "every file is tested" rule from CLAUDE.md §4 and
docs/TESTING_STRATEGY.md §4: for each ``src/l2arb/**/<module>.py`` there must be a
``tests/**/test_<module>.py`` somewhere in the test tree.

Excluded from the requirement:
  * ``__init__.py`` (package markers)
  * ``py.typed``
  * anything listed in ``ALLOWLIST`` below (e.g. pure ``Protocol`` port files
    that contain no executable logic — add with a justification comment).

Pure stdlib so it runs in pre-commit and CI with no project dependencies.
Exit code 0 = all paired; 1 = missing pairs (printed).
"""

from __future__ import annotations

from pathlib import Path

# Modules that legitimately need no dedicated test module. Keep this list short
# and justify every entry — an empty allowlist is the goal.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # e.g. "ports/rpc.py",  # pure typing.Protocol, no runtime logic
    }
)


def find_unpaired(src_root: Path, tests_root: Path) -> list[str]:
    """Return repo-relative source modules that have no ``test_<module>.py``.

    A module ``a/b/foo.py`` is satisfied by *any* ``test_foo.py`` anywhere under
    ``tests_root`` (the test tree need not mirror the source layout).
    """
    if not src_root.exists():
        return []

    existing_tests = {p.name for p in tests_root.rglob("test_*.py")}
    missing: list[str] = []

    for module in sorted(src_root.rglob("*.py")):
        if module.name == "__init__.py":
            continue
        rel = module.relative_to(src_root.parent.parent).as_posix()
        rel_from_pkg = module.relative_to(src_root).as_posix()
        if rel_from_pkg in ALLOWLIST:
            continue
        if f"test_{module.stem}.py" not in existing_tests:
            missing.append(rel)

    return missing


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    missing = find_unpaired(repo / "src" / "l2arb", repo / "tests")
    if missing:
        print("Untested runtime modules (add tests/**/test_<module>.py):")
        for rel in missing:
            print(f"  - {rel}")
        return 1
    print("test-pairing: OK — every runtime module has a paired test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
