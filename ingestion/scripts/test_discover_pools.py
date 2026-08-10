"""Offline unit tests for discover_pools.py — no network, deterministic.

Run with:  python3 -m unittest discover -s ingestion/scripts
(or, from within ingestion/scripts/:  python3 -m unittest test_discover_pools)

The end-to-end `discover_chain` test replays the *real*, already on-chain-
verified Arbitrum WETH/USDC pool from `config/pools/arbitrum.example.toml`
(address, token0/token1, fee) as canned RPC responses — a recorded-real fixture,
not an invented one — so a correct result here proves the ABI encode/decode
matches what a genuine Uniswap V3 factory and pool actually return, not just
that the test and the code agree with each other.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover_pools as dp  # noqa: E402

ZERO_WORD = "00" * 32


def _addr_word(addr: str) -> str:
    return dp._encode_address(addr)


def _uint_word(n: int) -> str:
    return dp._encode_uint(n)


class FakeRpc:
    """A minimal fake on-chain world: contracts keyed by (lowercased) address,
    each with code + a selector -> return-word dispatch table. Parses incoming
    calldata for real instead of string-matching the caller's exact bytes, so
    tests exercise the same encode/decode path production calls do."""

    def __init__(self):
        self.contracts: dict[str, dict] = {}

    def add_contract(self, address: str, responses: dict[str, str]):
        self.contracts[address.lower()] = {"code": "0x600160005260206000f3", "responses": responses}

    def eth_get_code(self, address: str) -> str:
        c = self.contracts.get(address.lower())
        return c["code"] if c else "0x"

    def eth_call(self, to: str, data: str) -> str:
        # Keyed on the *full* calldata (selector + args), not just the 4-byte
        # selector: feeAmountTickSpacing(500) and feeAmountTickSpacing(3000)
        # share a selector but must answer differently. A zero-arg call's full
        # calldata is just its selector, so this is still exact for token0()/
        # token1()/fee() too.
        c = self.contracts.get(to.lower())
        if c is None:
            raise dp.DiscoveryError(f"eth_call to unknown/no-code address {to}")
        key = data.removeprefix("0x")
        if key not in c["responses"]:
            raise dp.DiscoveryError(f"unexpected calldata {key} for {to}")
        return "0x" + c["responses"][key]


# ── Real, on-chain-verified Arbitrum fixture (config/pools/arbitrum.example.toml) ──
FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
POOL = "0xC6962004f452bE9203591991D15f6b388e09E8D0"
FEE = 500


def _real_factory_and_pool() -> FakeRpc:
    rpc = FakeRpc()
    responses = {
        dp.SEL_FEE_AMOUNT_TICK_SPACING + _uint_word(500): _uint_word(10),
        dp.SEL_FEE_AMOUNT_TICK_SPACING + _uint_word(3000): _uint_word(60),
    }
    # getPool is order-independent on a real factory (it looks up by canonical
    # token0/token1 regardless of the order the caller passes them in) — both
    # argument orderings must answer identically, so both are registered here
    # rather than only the one this module's own iteration order happens to use
    # today.
    for a, b in ((WETH, USDC), (USDC, WETH)):
        for fee in dp.DEFAULT_FEE_TIERS:
            key = dp.SEL_GET_POOL + _addr_word(a) + _addr_word(b) + _uint_word(fee)
            responses[key] = _addr_word(POOL) if fee == FEE else ZERO_WORD
    rpc.add_contract(FACTORY, responses)
    rpc.add_contract(
        POOL,
        {
            dp.SEL_TOKEN0: _addr_word(WETH),
            dp.SEL_TOKEN1: _addr_word(USDC),
            dp.SEL_FEE: _uint_word(FEE),
        },
    )
    return rpc


class EncodeDecodeTest(unittest.TestCase):
    def test_address_round_trips(self):
        word = dp._encode_address(WETH)
        self.assertEqual(len(word), 64)
        self.assertEqual(dp._decode_address(word).lower(), WETH.lower())

    def test_uint_encodes_big_endian_padded(self):
        self.assertEqual(dp._encode_uint(500), ZERO_WORD[:-3] + "1f4")

    def test_int24_decodes_known_tick_spacings(self):
        for fee, spacing in dp.KNOWN_TICK_SPACINGS.items():
            self.assertEqual(dp._decode_int24(dp._encode_uint(spacing)), spacing)


class FingerprintTest(unittest.TestCase):
    def test_accepts_a_genuine_factory(self):
        rpc = _real_factory_and_pool()
        dp.fingerprint_factory(rpc, FACTORY)  # must not raise

    def test_rejects_no_code(self):
        rpc = FakeRpc()
        with self.assertRaises(dp.DiscoveryError):
            dp.fingerprint_factory(rpc, FACTORY)

    def test_rejects_wrong_tick_spacing(self):
        rpc = FakeRpc()
        rpc.add_contract(
            FACTORY,
            {
                dp.SEL_FEE_AMOUNT_TICK_SPACING + _uint_word(500): _uint_word(999),  # not 10
                dp.SEL_FEE_AMOUNT_TICK_SPACING + _uint_word(3000): _uint_word(60),
            },
        )
        with self.assertRaises(dp.DiscoveryError):
            dp.fingerprint_factory(rpc, FACTORY)


class DiscoverPoolTest(unittest.TestCase):
    def test_finds_the_real_arbitrum_weth_usdc_pool(self):
        rpc = _real_factory_and_pool()
        found = dp.discover_pool(rpc, FACTORY, WETH, USDC, FEE)
        self.assertIsNotNone(found)
        self.assertEqual(found.address.lower(), POOL.lower())
        self.assertEqual(found.token0.lower(), WETH.lower())
        self.assertEqual(found.token1.lower(), USDC.lower())
        self.assertEqual(found.fee_pips, FEE)

    def test_zero_address_is_no_pool_not_an_error(self):
        rpc = _real_factory_and_pool()
        self.assertIsNone(dp.discover_pool(rpc, FACTORY, WETH, USDC, 10000))

    def test_pool_with_no_code_is_rejected(self):
        rpc = FakeRpc()
        rpc.add_contract(
            FACTORY,
            {dp.SEL_GET_POOL + _addr_word(WETH) + _addr_word(USDC) + _uint_word(FEE): _addr_word(POOL)},
        )
        # POOL is never added as a contract -> eth_getCode returns "0x"
        self.assertIsNone(dp.discover_pool(rpc, FACTORY, WETH, USDC, FEE))

    def test_fee_mismatch_is_rejected(self):
        rpc = FakeRpc()
        rpc.add_contract(
            FACTORY,
            {dp.SEL_GET_POOL + _addr_word(WETH) + _addr_word(USDC) + _uint_word(FEE): _addr_word(POOL)},
        )
        rpc.add_contract(
            POOL,
            {
                dp.SEL_TOKEN0: _addr_word(WETH),
                dp.SEL_TOKEN1: _addr_word(USDC),
                dp.SEL_FEE: _uint_word(3000),  # factory said 500, pool itself says 3000
            },
        )
        self.assertIsNone(dp.discover_pool(rpc, FACTORY, WETH, USDC, FEE))

    def test_token_mismatch_is_rejected(self):
        other_token = "0x0000000000000000000000000000000000dEaD"
        rpc = FakeRpc()
        rpc.add_contract(
            FACTORY,
            {dp.SEL_GET_POOL + _addr_word(WETH) + _addr_word(USDC) + _uint_word(FEE): _addr_word(POOL)},
        )
        rpc.add_contract(
            POOL,
            {
                dp.SEL_TOKEN0: _addr_word(WETH),
                dp.SEL_TOKEN1: _addr_word(other_token),  # not USDC
                dp.SEL_FEE: _uint_word(FEE),
            },
        )
        self.assertIsNone(dp.discover_pool(rpc, FACTORY, WETH, USDC, FEE))


class DiscoverChainTest(unittest.TestCase):
    def test_end_to_end_against_the_real_arbitrum_fixture(self):
        rpc = _real_factory_and_pool()
        result = dp.discover_chain(
            "arbitrum", "unused-when-rpc-is-injected", FACTORY, {"WETH": WETH, "USDC": USDC}, rpc=rpc
        )
        self.assertTrue(result.fingerprint_ok)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.pools), 1)
        self.assertEqual(result.pools[0].address.lower(), POOL.lower())

    def test_bad_factory_reports_a_clean_error_not_a_crash(self):
        rpc = FakeRpc()  # no contracts registered -> factory has no code
        result = dp.discover_chain("base", "unused", FACTORY, {"WETH": WETH, "USDC": USDC}, rpc=rpc)
        self.assertFalse(result.fingerprint_ok)
        self.assertEqual(result.pools, [])
        self.assertIsNotNone(result.error)


class RenderTomlTest(unittest.TestCase):
    def test_renders_a_parseable_pool_block(self):
        result = dp.DiscoveryResult(
            chain="arbitrum",
            factory=FACTORY,
            fingerprint_ok=True,
            pools=[dp.DiscoveredPool(dex="uniswap_v3", address=POOL, fee_pips=FEE, token0=WETH, token1=USDC, factory=FACTORY)],
        )
        text = dp.render_toml(result)
        self.assertIn('kind     = "v3"', text)
        self.assertIn(f'address  = "{POOL}"', text)
        self.assertIn("fee_pips = 500", text)

        if sys.version_info >= (3, 11):
            import tomllib

            parsed = tomllib.loads(text)
            self.assertEqual(len(parsed["pool"]), 1)
            self.assertEqual(parsed["pool"][0]["address"], POOL)


class TokenArgParsingTest(unittest.TestCase):
    def test_overrides_and_extends_base_tokens(self):
        tokens = dp._parse_token_args(["USDT=0xdead000000000000000000000000000000beef"], {"WETH": WETH})
        self.assertEqual(tokens["WETH"], WETH)
        self.assertEqual(tokens["USDT"], "0xdead000000000000000000000000000000beef")

    def test_rejects_malformed_pair(self):
        with self.assertRaises(SystemExit):
            dp._parse_token_args(["not-a-pair"], {})


if __name__ == "__main__":
    unittest.main()
