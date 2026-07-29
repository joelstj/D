"""Read-only HTTP service exposing the calculation engine over JSON.

A thin FastAPI app for backends that prefer HTTP to a subprocess. It is **read
only**: it computes and returns opportunities; it holds no keys and executes
nothing (ADR-001). ``POST /detect`` takes a :class:`DetectRequest` and returns the
ranked top-N; ``GET /health`` is a liveness probe.

The heavy lifting lives in :func:`l2arb.api.service.detect`; this module is only
the transport, so the HTTP and stdin paths can never diverge in behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from l2arb.api.schema import DetectRequest
from l2arb.api.service import detect
from l2arb.graph.tropical import warmup
from l2arb.logging import configure_logging

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Both are one-time, off-the-hot-path setup: real logging from
    # L2ARB__LOG_LEVEL (previously dead config, see l2arb.logging), and paying
    # numba's JIT compilation cost here instead of on the first real /detect
    # touching a >=4-hop sweep (graph/tropical.py's own documented gotcha).
    configure_logging()
    warmup()
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application (factory so tests get isolated instances)."""
    app = FastAPI(
        title="l2arb — L2 arbitrage detection engine",
        description="Read-only arbitrage opportunity detection. Detection only; no execution.",
        version="0.1.0",
        lifespan=_lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/detect")
    def detect_endpoint(request: DetectRequest) -> dict[str, Any]:
        return detect(request)

    return app


app = create_app()
