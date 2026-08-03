#!/usr/bin/env python3
"""End-to-end integration smoke test for the whole D stack.

Proves the four components wire together on REAL data, in one process tree:

    real Arbitrum pool state  ->  engine /detect  ->  (WS Envelope, seam B)
      ->  dashboard ExternalProvider -> engineMap -> REST /api  ->  UI controls

It starts the Python engine and the Node dashboard backend, feeds the dashboard
the engine's own DetectResponse over the same versioned WS envelope the Rust
`l2-ingest` sink emits, and then exercises every runtime control (settings,
engine toggle, one-click execute) plus the LiveExecutor safety gate.

Data integrity: every reserve/price/gas figure is read live from Arbitrum. One
clearly-labeled integration fixture (a manufactured price dislocation, exactly
like the contracts' fork test) is injected as a SECOND detect scenario so the
board is populated — the engine still does all the math. Nothing synthetic
reaches a shipped runtime path.

Run it with the engine's virtualenv (it needs web3 + websockets)::

    cd engine && uv run python ../scripts/e2e_smoke.py

It SKIPS cleanly (exit 0) when a prerequisite is missing — an unbuilt dashboard,
a missing venv dep, or no outbound Arbitrum RPC — so it is safe in offline CI.
Exit 0 = pass or skip; exit 1 = a real integration failure.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "engine"
BACKEND_DIR = ROOT / "dashboard" / "backend"
RPC = os.environ.get("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
CHAIN_ID = 42161
ENGINE_PORT, DASH_PORT, FEED_PORT = 8080, 8787, 9010

# Real Arbitrum addresses (WETH/USDC across two DEXes) — from the ingestion registry.
WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
V3_POOL = "0xC6962004f452bE9203591991D15f6b388e09E8D0"  # Uniswap V3 0.05%
V2_POOL = "0x54b26fAF3671677C19f70C4b879a6f7B898F732c"  # Camelot V2

_passed = 0
_failed = 0


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    global _failed
    _failed += 1
    print(f"  ❌ {msg}")


def skip(msg: str) -> None:
    print(f"SKIP e2e_smoke: {msg}")
    raise SystemExit(0)


def http_json(url: str, method: str = "GET", body: dict | None = None, timeout: float = 15.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


def wait_http(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# --feed mode: serve the DetectResponse as an ingestion WS Envelope.
# ---------------------------------------------------------------------------
def run_feed(detect_path: str) -> None:
    import asyncio
    import websockets

    payload = json.loads(Path(detect_path).read_text())

    def frame() -> str:
        # Stamp a fresh single-host wall-clock origin per send so the dashboard can
        # compute a real end-to-end latency (mirrors the Rust ingestion envelope).
        return json.dumps(
            {
                "schema_version": 1,
                "kind": "opportunities",
                "chain_blocks": {str(CHAIN_ID): 0},
                "latency": {
                    "origin_wall_ms": int(time.time() * 1000),
                    "component": "ingestion",
                    "stages": [{"stage": "build", "ms": 0.5}, {"stage": "engine_roundtrip", "ms": 6.0}],
                    "total_ms": 6.5,
                },
                "payload": payload,
            }
        )

    async def handler(ws):
        try:
            while True:
                await ws.send(frame())
                await asyncio.sleep(1)
        except Exception:
            return

    async def main():
        async with websockets.serve(handler, "127.0.0.1", FEED_PORT):
            await asyncio.Future()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Build a DetectRequest from live pool state.
# ---------------------------------------------------------------------------
def read_live_request():
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(RPC))
    if w3.eth.chain_id != CHAIN_ID:
        skip(f"ARBITRUM_RPC_URL is chain {w3.eth.chain_id}, not Arbitrum")
    blk = w3.eth.get_block("latest")
    h = blk["hash"].hex()
    block_hash = (h if h.startswith("0x") else "0x" + h).lower()
    gas_price = int(w3.eth.gas_price)

    v3 = w3.eth.contract(address=Web3.to_checksum_address(V3_POOL), abi=[
        {"name": "slot0", "inputs": [], "stateMutability": "view", "type": "function", "outputs": [
            {"type": "uint160"}, {"type": "int24"}, {"type": "uint16"}, {"type": "uint16"},
            {"type": "uint16"}, {"type": "uint8"}, {"type": "bool"}]},
        {"name": "liquidity", "inputs": [], "stateMutability": "view", "type": "function",
         "outputs": [{"type": "uint128"}]}])
    s0 = v3.functions.slot0().call()
    sqrt_price_x96, tick, liquidity = int(s0[0]), int(s0[1]), int(v3.functions.liquidity().call())

    v2 = w3.eth.contract(address=Web3.to_checksum_address(V2_POOL), abi=[
        {"name": "getReserves", "inputs": [], "stateMutability": "view", "type": "function",
         "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint16"}, {"type": "uint16"}]}])
    r = v2.functions.getReserves().call()
    reserve0, reserve1 = int(r[0]), int(r[1])
    eth_in_usdc = (sqrt_price_x96 ** 2) / (2 ** 192) * (10 ** (18 - 6))
    print(f"Arbitrum block {blk['number']}  ETH~{eth_in_usdc:,.0f} USDC  gas={gas_price} wei")

    def token(addr, dec, sym):
        return {"chain_id": CHAIN_ID, "address": addr.lower(), "decimals": dec, "symbol": sym, "quarantined": False}

    def stamp():
        return {"chain_id": CHAIN_ID, "number": blk["number"], "block_hash": block_hash, "timestamp": int(blk["timestamp"])}

    pools = [
        {"address": V3_POOL.lower(), "kind": "v3", "fee_pips": 500, "verified": True,
         "token0": token(WETH, 18, "WETH"), "token1": token(USDC, 6, "USDC"), "blockstamp": stamp(),
         "v3": {"sqrt_price_x96": str(sqrt_price_x96), "tick": tick, "liquidity": str(liquidity)}},
        {"address": V2_POOL.lower(), "kind": "v2", "fee_pips": 300, "verified": True,
         "token0": token(WETH, 18, "WETH"), "token1": token(USDC, 6, "USDC"), "blockstamp": stamp(),
         "v2": {"reserve0": str(reserve0), "reserve1": str(reserve1)}},
    ]
    request = {
        "top_n": 10, "max_hops": 4,
        "chains": [{"chain_id": CHAIN_ID, "gas_price_wei": gas_price, "l1_data_fee_wei": 0,
                    "min_profit_bps": 1.0, "native_price_in": {WETH.lower(): 1.0, USDC.lower(): eth_in_usdc},
                    "hubs": [WETH.lower(), USDC.lower()]}],
        "pools": pools,
    }
    return request, eth_in_usdc


def main() -> int:
    # Preflight — skip cleanly if the environment can't support the test.
    try:
        import web3  # noqa: F401
        import websockets  # noqa: F401
    except Exception as e:
        skip(f"missing venv deps ({e}); run via `cd engine && uv run python ../scripts/e2e_smoke.py`")
    if not (BACKEND_DIR / "dist" / "index.js").exists():
        skip("dashboard backend not built (run `pnpm --dir dashboard build`)")
    try:
        from web3 import Web3
        if Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 8})).eth.chain_id != CHAIN_ID:
            skip(f"ARBITRUM_RPC_URL is not Arbitrum One (chain {CHAIN_ID})")
    except SystemExit:
        raise
    except Exception as e:
        skip(f"no outbound Arbitrum RPC ({e})")

    procs: list[subprocess.Popen] = []
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / "e2e_smoke"
    scratch.mkdir(parents=True, exist_ok=True)
    detect_path = scratch / "detect_response.json"

    try:
        # 1. Engine
        eng = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "l2arb.api.http:app", "--host", "127.0.0.1", "--port", str(ENGINE_PORT)],
            cwd=ENGINE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(eng)
        if not wait_http(f"http://127.0.0.1:{ENGINE_PORT}/health"):
            bad("engine did not become healthy")
            return 1
        ok("engine up and healthy (/health)")

        # 2. Live detect — honest scenario (efficient market -> 0 is correct).
        request, eth_in_usdc = read_live_request()
        st, honest = http_json(f"http://127.0.0.1:{ENGINE_PORT}/detect", "POST", request)
        if st == 200:
            ok(f"engine ran detection on live pool state (honest count={honest['count']})")
        else:
            bad(f"engine /detect failed: {st} {honest}")
            return 1

        # 3. Labeled integration fixture — manufacture a real dislocation so the
        #    engine computes a genuine opportunity to drive the dashboard.
        import copy
        disloc = copy.deepcopy(request)
        disloc["pools"][1]["v2"] = {"reserve0": str(500 * 10 ** 18),
                                    "reserve1": str(int(500 * eth_in_usdc * 0.985 * 10 ** 6))}
        disloc["pools"][1]["address"] = "0x000000000000000000000000000000000000dead"
        st, resp = http_json(f"http://127.0.0.1:{ENGINE_PORT}/detect", "POST", disloc)
        if st == 200 and resp["count"] >= 1:
            o = resp["opportunities"][0]
            ok(f"engine detected + sized a real opportunity ({o['strategy']}, {o['profit_bps']:.1f} bps, verified={o['verified']})")
        else:
            bad(f"engine failed to detect on the dislocation fixture: {st} {resp}")
            return 1
        # Latency-health: the /detect response carries the engine's per-stage timing.
        timing = resp.get("timing") or {}
        stages = [s.get("stage") for s in timing.get("stages", [])]
        if timing.get("component") == "engine" and stages == ["build", "detect", "rank", "serialize"]:
            ok(f"engine reports per-stage timing (total {timing.get('total_ms')}ms)")
        else:
            bad(f"engine /detect response missing the timing block: {timing}")
        detect_path.write_text(json.dumps(resp))

        # 4. Feed server (ingestion WS sink stand-in)
        feed = subprocess.Popen([sys.executable, __file__, "--feed", str(detect_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(feed)
        time.sleep(1)

        # 5. Dashboard backend in external mode, paper execution
        env = {**os.environ, "DATA_SOURCE": "external", "INGEST_FEED_URL": f"ws://127.0.0.1:{FEED_PORT}",
               "PORT": str(DASH_PORT), "EXECUTION_MODE": "paper"}
        dash = subprocess.Popen(["node", "dist/index.js"], cwd=BACKEND_DIR, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(dash)
        api = f"http://127.0.0.1:{DASH_PORT}/api"
        if not wait_http(f"{api}/health"):
            bad("dashboard did not become healthy")
            return 1
        _, health = http_json(f"{api}/health")
        ok(f"dashboard up (dataSource={health.get('dataSource')}, execution={health.get('executionMode')})")

        # 6. Lower the profit gate so the WETH-numeraire opp surfaces (its profit
        #    is a WETH magnitude, honestly not USD-converted), then wait for it.
        http_json(f"{api}/settings", "PATCH", {"minProfitUsd": 0, "minProfitBps": 0})
        oid = ""
        for _ in range(25):
            _, opps = http_json(f"{api}/opportunities")
            rows = opps.get("opportunities", [])
            if rows:
                oid = rows[0]["id"]
                break
            time.sleep(1)
        if oid:
            ok("real engine opportunity flowed through the WS seam to the dashboard")
        else:
            bad("no opportunity surfaced from the external feed")
            return 1

        # 6b. Latency-health: the dashboard aggregated the end-to-end trace and the
        #     separate execution-readiness probe is read-only + unconfigured in paper.
        _, lat = http_json(f"{api}/latency")
        comps = {c.get("component") for c in lat.get("components", [])}
        if lat.get("anchored") and lat.get("endToEnd") and {"engine", "dashboard"} <= comps:
            e2e = lat["endToEnd"]
            ok(f"pipeline latency aggregated end-to-end (p50 {e2e.get('p50')}ms across {comps})")
        else:
            bad(f"/api/latency did not aggregate the pipeline trace: {lat}")
        _, xlat = http_json(f"{api}/health/execution")
        if xlat.get("configured") is False and xlat.get("healthy") is False:
            ok("execution-readiness probe is read-only + unconfigured in paper (no broadcast path)")
        else:
            bad(f"execution probe unexpected in paper mode: {xlat}")

        # 7. Runtime controls
        _, patched = http_json(f"{api}/settings", "PATCH", {"slippageBps": 75, "maxGasGwei": 30})
        (ok if patched.get("slippageBps") == 75 else bad)("settings PATCH applied (sliders wired)")
        _, tog = http_json(f"{api}/engine/toggle", "POST", {"enabled": False})
        (ok if tog.get("engineEnabled") is False else bad)("engine on/off toggle works")
        http_json(f"{api}/engine/toggle", "POST", {"enabled": True})

        # 8. One-click execute (paper -> simulated fill, no broadcast)
        st, ex = http_json(f"{api}/execute/{oid}", "POST", {})
        if st == 200 and str(ex.get("status")) in {"filled", "success", "reverted"}:
            ok(f"paper execute -> simulated fill ({ex.get('status')}, mode={ex.get('mode')}, no broadcast)")
        else:
            bad(f"paper execute unexpected: {st} {ex}")

        # 9. LiveExecutor MUST refuse to broadcast
        # cooldownMs=0 so step 8's paper execute (same "arbitrum" network,
        # moments ago) can't shadow this check behind an unrelated
        # riskLimitBlock() cooldown rejection — executeOpportunity() checks
        # riskLimitBlock() before it ever selects an executor, so without this
        # the assertion below can fail on "cooldown active" instead of
        # genuinely exercising (or failing to exercise) LiveExecutor at all.
        http_json(f"{api}/settings", "PATCH", {"executionMode": "live", "cooldownMs": 0})
        lid = ""
        for _ in range(15):
            _, opps = http_json(f"{api}/opportunities")
            rows = opps.get("opportunities", [])
            if rows:
                lid = rows[0]["id"]
                break
            time.sleep(1)
        if lid:
            _, lx = http_json(f"{api}/execute/{lid}", "POST", {})
            msg = json.dumps(lx).lower()
            if "not enabled" in msg or "live execution" in msg or "refus" in msg:
                ok("LiveExecutor refuses to broadcast (paper-by-default safety gate holds)")
            else:
                bad(f"LiveExecutor did NOT refuse (SAFETY REGRESSION): {lx}")
        else:
            bad("could not obtain a fresh opp to test live refusal")

        print(f"\nE2E RESULT: {_passed} passed, {_failed} failed")
        return 0 if _failed == 0 else 1
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGTERM)
            except Exception:
                pass
        time.sleep(1)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--feed":
        run_feed(sys.argv[2])
    else:
        sys.exit(main())
