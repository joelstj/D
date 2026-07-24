"""Dependency-free Python client for the L2 Arbitrage GUI API.

Uses only the standard library for REST. Optional real-time streaming is
available if the ``websocket-client`` package is installed.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional


class L2ApiError(RuntimeError):
    """Raised when the API returns a non-2xx response."""


class L2ArbitrageClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8787",
        api_key: Optional[str] = None,
        ws_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ws_url = ws_url or (self.base_url.replace("http", "ws", 1) + "/ws")
        self.timeout = timeout

    # -- REST ---------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        if self.api_key:
            req.add_header("authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:  # pragma: no cover - network error path
            detail = exc.read().decode(errors="replace")
            raise L2ApiError(f"{exc.code} {exc.reason}: {detail}") from exc

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def networks(self) -> dict:
        return self._request("GET", "/api/networks")

    def get_settings(self) -> dict:
        return self._request("GET", "/api/settings")

    def update_settings(self, patch: dict) -> dict:
        """Merge a partial settings update; takes effect immediately."""
        return self._request("PATCH", "/api/settings", patch)

    def reset_settings(self) -> dict:
        return self._request("POST", "/api/settings/reset")

    def opportunities(self, limit: int = 100, network: Optional[str] = None) -> dict:
        query = {"limit": str(limit)}
        if network:
            query["network"] = network
        return self._request("GET", "/api/opportunities?" + urllib.parse.urlencode(query))

    def stats(self) -> dict:
        return self._request("GET", "/api/stats")

    def execute(self, opportunity_id: str) -> dict:
        return self._request("POST", f"/api/execute/{opportunity_id}")

    def set_engine_enabled(self, enabled: bool) -> dict:
        return self._request("POST", "/api/engine/toggle", {"enabled": enabled})

    # -- Streaming (optional) ----------------------------------------------
    def stream(self, on_message: Callable[[dict], None]) -> None:
        """Block and forward real-time messages to ``on_message``.

        Requires ``pip install websocket-client``. Each message is a dict with
        ``type``, ``payload`` and ``ts`` keys.
        """
        try:
            import websocket  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise L2ApiError(
                "Real-time streaming needs the optional dependency: pip install websocket-client"
            ) from exc

        def _on_message(_ws, message: str) -> None:
            try:
                on_message(json.loads(message))
            except json.JSONDecodeError:
                pass

        ws = websocket.WebSocketApp(self.ws_url, on_message=_on_message)
        ws.run_forever()
