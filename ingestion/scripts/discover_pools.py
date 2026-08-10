#!/usr/bin/env python3
"""Discover real, on-chain Uniswap-V3-style pools and emit a pool registry TOML.

`config/pools/README.md` documents this component's pool registries as
intentionally uncurated ("Pool addresses are intentionally not committed as a
curated set here... a discovery script can seed them") — this is that script.

Method (deliberately *not* a `PairCreated`/`PoolCreated` log crawl, which would
mean scanning millions of blocks through a public RPC): a Uniswap V3 Factory
exposes `getPool(tokenA, tokenB, fee)`, a single cheap read that returns the
pool address for a given pair + fee tier directly, with no history to scan. So
for a small, named set of major tokens (WETH/USDC by default) this needs only a
handful of `eth_call`s per chain.

Nothing here is ever trusted blind:
  1. the candidate factory address is *fingerprinted* on-chain first
     (`feeAmountTickSpacing` for two different standard fee tiers must return
     the values every genuine Uniswap V3 factory returns) — a wrong or
     look-alike address fails this and the chain is cleanly skipped, never
     silently treated as real;
  2. every pool `getPool` returns is independently re-checked: code exists,
     and its own `token0()`/`token1()`/`fee()` match what was asked for;
  3. every entry this script emits is *still* re-proven on-chain by the real
     startup validation gate (`crates/registry/src/gate.rs`) before it enters
     the live set — this script only proposes candidates.

The four selectors used below (`token0`, `token1`, `fee`, `getPool`,
`feeAmountTickSpacing`) are hardcoded raw 4-byte constants rather than computed,
because computing them correctly needs a real Keccak256 (not the same as the
stdlib's `hashlib.sha3_256`, which is the differently-padded NIST SHA3) and
pulling in a dependency just for that felt worse than pinning well-known,
stable ABI constants. They are not this script's own claim: they are asserted
against alloy's real, tested Keccak256 in
`ingestion/crates/registry/src/abi.rs::abi::tests::selectors_are_stable` — this
file is the second, independent consumer of that one proven source of truth,
not a second guess at it.

Usage:
    python3 discover_pools.py --chain base --http-url https://... [--out config/pools/base.toml]
    python3 discover_pools.py --chain base --http-url https://... --json   # machine-readable

Exits 0 with >=1 discovered pool, 1 if the factory couldn't be fingerprinted or
zero pools were found, 2 on a usage/argument error. Stdlib only — no pip install.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ── Hardcoded, tested selectors (see module docstring) ───────────────────────
SEL_TOKEN0 = "0dfe1681"
SEL_TOKEN1 = "d21220a7"
SEL_FEE = "ddca3f43"
SEL_GET_POOL = "1698ee82"
SEL_FEE_AMOUNT_TICK_SPACING = "22afcccb"

ZERO_ADDRESS = "0x" + "00" * 20

# Fee tier (millionths) -> the tick spacing a genuine Uniswap V3 factory
# reports for it. Two of these are checked as the on-chain fingerprint.
KNOWN_TICK_SPACINGS = {100: 1, 500: 10, 3000: 60, 10000: 200}
FINGERPRINT_FEE_TIERS = (500, 3000)
DEFAULT_FEE_TIERS = (100, 500, 3000, 10000)

# ── Known candidates (see module docstring §1 — always fingerprinted, never
# trusted blind). Sourced from this repo's own already-recorded address book
# (`contracts/config/addresses.js`) and, for Arbitrum, the real pool already
# committed at `config/pools/arbitrum.example.toml`. Unichain and Ink are
# deliberately absent: Unichain's native liquidity is Uniswap V4 (a different
# discovery mechanism — no per-pool contract, identity is a poolId over a
# PoolManager singleton) and no Uniswap V3 factory address for Ink is
# confidently known here — both fall back to the guided prompt instead of a
# guess.
CHAIN_CANDIDATES: dict[str, dict] = {
    "arbitrum": {
        "chain_id": 42161,
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "tokens": {
            "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        },
    },
    "base": {
        "chain_id": 8453,
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "tokens": {
            "WETH": "0x4200000000000000000000000000000000000006",
            "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        },
    },
    "optimism": {
        "chain_id": 10,
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "tokens": {
            "WETH": "0x4200000000000000000000000000000000000006",
            "USDC": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        },
    },
}


class DiscoveryError(RuntimeError):
    """A clean, explained failure to auto-discover — never a fabricated result."""


class RpcClient:
    """Minimal JSON-RPC 2.0 client over stdlib `urllib` (no pip install)."""

    def __init__(self, url: str, timeout: float = 12.0):
        self.url = url
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: list) -> object:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        # A real browser-like User-Agent matters here: several RPC gateways sit
        # behind Cloudflare (or similar) bot protection that 403s the stdlib
        # default (`Python-urllib/3.x`) outright, before the request ever reaches
        # the node — empirically confirmed against a live endpoint while building
        # this script, not a defensive guess.
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) l2-ingest-pool-discovery/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                parsed = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError) as e:
            raise DiscoveryError(f"RPC transport error calling {method}: {e}") from e
        if "error" in parsed:
            raise DiscoveryError(f"RPC error calling {method}: {parsed['error']}")
        return parsed["result"]

    def eth_call(self, to: str, data: str) -> str:
        return self.call("eth_call", [{"to": to, "data": data}, "latest"])

    def eth_get_code(self, address: str) -> str:
        return self.call("eth_getCode", [address, "latest"])


# ── ABI encode/decode (fixed-width, no external deps) ────────────────────────


def _encode_address(addr: str) -> str:
    return addr.lower().removeprefix("0x").rjust(64, "0")


def _encode_uint(n: int) -> str:
    return format(n, "x").rjust(64, "0")


def _decode_address(word_hex: str) -> str:
    return "0x" + word_hex[-40:]


def _decode_int24(word_hex: str) -> int:
    v = int(word_hex, 16)
    if v >= 2**255:  # negative (two's complement) — never a real tick spacing
        v -= 2**256
    return v


def _has_code(rpc: RpcClient, address: str) -> bool:
    code = rpc.eth_get_code(address)
    return bool(code) and code not in ("0x", "0x0")


def _call_word(rpc: RpcClient, to: str, selector: str, *args_hex: str) -> str:
    ret = rpc.eth_call(to, "0x" + selector + "".join(args_hex))
    body = ret.removeprefix("0x")
    if len(body) < 64:
        raise DiscoveryError(f"short return from {to}: {ret!r}")
    return body[:64]


# ── Discovery logic ───────────────────────────────────────────────────────────


@dataclass
class DiscoveredPool:
    dex: str
    address: str
    fee_pips: int
    token0: str
    token1: str
    factory: str


@dataclass
class DiscoveryResult:
    chain: str
    factory: str
    fingerprint_ok: bool
    pools: list = field(default_factory=list)  # list[DiscoveredPool]
    error: str | None = None


def fingerprint_factory(rpc: RpcClient, factory: str) -> None:
    """Raise `DiscoveryError` unless `factory` behaves like a genuine Uniswap V3
    Factory for two independent, standard fee tiers. Never trusts an address
    (recalled, configured, or user-supplied) on shape/reputation alone."""
    if not _has_code(rpc, factory):
        raise DiscoveryError(f"no contract code at candidate factory {factory}")
    for fee in FINGERPRINT_FEE_TIERS:
        expected = KNOWN_TICK_SPACINGS[fee]
        word = _call_word(rpc, factory, SEL_FEE_AMOUNT_TICK_SPACING, _encode_uint(fee))
        got = _decode_int24(word)
        if got != expected:
            raise DiscoveryError(
                f"{factory} failed the V3-factory fingerprint: feeAmountTickSpacing({fee}) "
                f"returned {got}, expected {expected} — this does not look like a real "
                "Uniswap V3 factory on this chain"
            )


def discover_pool(rpc: RpcClient, factory: str, token_a: str, token_b: str, fee: int) -> DiscoveredPool | None:
    """One `getPool` lookup, independently re-verified. Returns `None` when no
    pool exists for this pair/fee (not an error — most fee tiers won't exist)."""
    word = _call_word(rpc, factory, SEL_GET_POOL, _encode_address(token_a), _encode_address(token_b), _encode_uint(fee))
    pool = _decode_address(word)
    if pool.lower() == ZERO_ADDRESS:
        return None
    if not _has_code(rpc, pool):
        return None  # getPool claimed a pool but it has no code — do not trust it

    token0 = _decode_address(_call_word(rpc, pool, SEL_TOKEN0))
    token1 = _decode_address(_call_word(rpc, pool, SEL_TOKEN1))
    onchain_fee = _decode_int24(_call_word(rpc, pool, SEL_FEE))
    if onchain_fee != fee:
        return None  # inconsistent with what we searched under — do not trust it
    if {token0.lower(), token1.lower()} != {token_a.lower(), token_b.lower()}:
        return None  # pool's own tokens don't match the pair we asked for

    return DiscoveredPool(
        dex="uniswap_v3", address=pool, fee_pips=fee, token0=token0, token1=token1, factory=factory
    )


def discover_chain(
    chain: str,
    http_url: str,
    factory: str,
    tokens: dict[str, str],
    fee_tiers: tuple[int, ...] = DEFAULT_FEE_TIERS,
    rpc: RpcClient | None = None,
) -> DiscoveryResult:
    """Discover pools for every distinct pair in `tokens` across `fee_tiers`.
    Never raises for an ordinary "nothing found" outcome — that comes back as
    `pools == []`; only genuine RPC/transport failures propagate. `rpc` is a
    test seam (inject a fake) — production callers always omit it."""
    rpc = rpc or RpcClient(http_url)
    try:
        fingerprint_factory(rpc, factory)
    except DiscoveryError as e:
        return DiscoveryResult(chain=chain, factory=factory, fingerprint_ok=False, error=str(e))

    names = sorted(tokens)
    pools: list[DiscoveredPool] = []
    for i, name_a in enumerate(names):
        for name_b in names[i + 1 :]:
            for fee in fee_tiers:
                found = discover_pool(rpc, factory, tokens[name_a], tokens[name_b], fee)
                if found is not None:
                    pools.append(found)
    return DiscoveryResult(chain=chain, factory=factory, fingerprint_ok=True, pools=pools)


# ── Rendering ──────────────────────────────────────────────────────────────


def render_toml(result: DiscoveryResult) -> str:
    lines = [
        f"# {result.chain} pool registry — auto-discovered by scripts/discover_pools.py.",
        f"# Factory {result.factory} was fingerprinted on-chain before use (see the",
        "# module docstring); every entry below is re-proven on-chain again by the",
        "# startup validation gate (docs/ARCHITECTURE.md §7) before it enters the live",
        "# set. Curate freely — add, remove, or hand-edit entries.",
        "",
    ]
    for p in result.pools:
        lines += [
            "[[pool]]",
            f'dex      = "{p.dex}"',
            'kind     = "v3"',
            f'address  = "{p.address}"',
            f"fee_pips = {p.fee_pips}",
            f'token0   = "{p.token0}"',
            f'token1   = "{p.token1}"',
            f'factory  = "{p.factory}"',
            "",
        ]
    return "\n".join(lines)


def _result_to_json(result: DiscoveryResult) -> dict:
    return {
        "chain": result.chain,
        "factory": result.factory,
        "fingerprint_ok": result.fingerprint_ok,
        "error": result.error,
        "pools": [vars(p) for p in result.pools],
        "toml": render_toml(result) if result.pools else None,
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_token_args(pairs: list[str], base: dict[str, str]) -> dict[str, str]:
    tokens = dict(base)
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--token expects NAME=0xADDRESS, got {pair!r}")
        name, addr = pair.split("=", 1)
        tokens[name.strip().upper()] = addr.strip()
    return tokens


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--chain", required=True, help="chain name, e.g. arbitrum | base | optimism")
    p.add_argument("--http-url", required=True, help="HTTPS RPC endpoint (the first, if comma-separated)")
    p.add_argument("--factory", help="override the candidate Uniswap V3 factory address")
    p.add_argument(
        "--token",
        action="append",
        default=[],
        metavar="NAME=0xADDRESS",
        help="add/override a token to pair up (repeatable); defaults to this chain's known WETH+USDC",
    )
    p.add_argument("--fee-tiers", default=",".join(str(f) for f in DEFAULT_FEE_TIERS))
    p.add_argument("--out", help="write the discovered registry TOML here (only if >=1 pool found)")
    p.add_argument("--json", action="store_true", help="print a machine-readable JSON result instead of TOML")
    args = p.parse_args(argv)

    chain = args.chain.strip().lower()
    candidate = CHAIN_CANDIDATES.get(chain, {})
    factory = args.factory or candidate.get("factory")
    tokens = _parse_token_args(args.token, candidate.get("tokens", {}))
    fee_tiers = tuple(int(f) for f in args.fee_tiers.split(",") if f.strip())

    if not factory:
        result = DiscoveryResult(
            chain=chain,
            factory="",
            fingerprint_ok=False,
            error=f"no known Uniswap V3 factory candidate for chain {chain!r} — pass --factory to try one",
        )
    elif len(tokens) < 2:
        result = DiscoveryResult(
            chain=chain,
            factory=factory,
            fingerprint_ok=False,
            error="need at least 2 tokens to pair up — pass --token NAME=0xADDRESS at least twice",
        )
    else:
        try:
            result = discover_chain(chain, args.http_url, factory, tokens, fee_tiers)
        except DiscoveryError as e:
            result = DiscoveryResult(chain=chain, factory=factory, fingerprint_ok=False, error=str(e))

    if args.json:
        print(json.dumps(_result_to_json(result), indent=2))
    elif result.error:
        print(f"discovery failed for {chain}: {result.error}", file=sys.stderr)
    elif not result.pools:
        print(f"factory fingerprinted OK but found 0 pools for {chain} — nothing to write", file=sys.stderr)
    else:
        toml_text = render_toml(result)
        if args.out:
            with open(args.out, "w") as f:
                f.write(toml_text)
            print(f"wrote {len(result.pools)} pool(s) to {args.out}", file=sys.stderr)
        else:
            print(toml_text)

    return 1 if (result.error or not result.pools) else 0


if __name__ == "__main__":
    raise SystemExit(main())
