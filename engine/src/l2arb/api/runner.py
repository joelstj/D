"""Stdin/stdout JSON batch runner — the zero-dependency integration path.

Any language can integrate with **no network and no bindings**: spawn
``python -m l2arb.api.runner``, write one JSON :class:`DetectRequest` to stdin,
read the JSON response from stdout. This makes the engine a drop-in subprocess for
Rust, Go, Node, C#, C++, JVM — anything that can pipe bytes.

On success it writes ``{"count", "opportunities"}`` and exits 0. On a malformed
request (bad JSON, failed validation, un-decodable pool) it writes
``{"error", "type"}`` and exits 1 — the error is structured JSON too, so the
caller never has to parse a stack trace.
"""

from __future__ import annotations

import sys
from typing import IO, Any

import orjson

from l2arb.api.service import run_detection

__all__ = ["main", "process"]


def process(raw: bytes) -> tuple[dict[str, Any], int]:
    """Turn raw request bytes into a ``(response, exit_code)`` pair (pure, testable)."""
    try:
        request = orjson.loads(raw)
        return run_detection(request), 0
    except Exception as exc:
        return {"error": str(exc), "type": type(exc).__name__}, 1


def main(stdin: IO[bytes] | None = None, stdout: IO[bytes] | None = None) -> int:
    """Read a request from stdin, write the response to stdout, return the exit code."""
    source = stdin if stdin is not None else sys.stdin.buffer
    sink = stdout if stdout is not None else sys.stdout.buffer
    response, code = process(source.read())
    sink.write(orjson.dumps(response))
    sink.flush()
    return code


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
