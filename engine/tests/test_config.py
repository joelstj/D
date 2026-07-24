"""Unit tests for l2arb.config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from l2arb.config import ChainEndpoints, Settings, get_settings

pytestmark = pytest.mark.unit


def test_defaults_are_sane() -> None:
    s = Settings()
    assert s.min_profit_bps == 5
    assert s.max_hops == 4
    assert s.max_latency_ms_p99 == 250
    assert s.gas_safety_multiplier == pytest.approx(1.5)
    assert s.chains == {}
    assert s.enabled_chains == []


def test_chain_endpoints_split_csv_string() -> None:
    ep = ChainEndpoints(http="https://a.example, https://b.example")
    assert ep.http_urls == ("https://a.example", "https://b.example")
    assert ep.wss_urls == ()


def test_chain_endpoints_single_endpoint() -> None:
    ep = ChainEndpoints(http="https://a.example", wss="wss://a.example")
    assert ep.http_urls == ("https://a.example",)
    assert ep.wss_urls == ("wss://a.example",)


def test_enabled_chains_filters_and_sorts() -> None:
    s = Settings(
        chains={
            "optimism": ChainEndpoints(http="https://opt"),
            "arbitrum": ChainEndpoints(http="https://arb"),
            "empty": ChainEndpoints(),  # no http -> excluded
        }
    )
    assert s.enabled_chains == ["arbitrum", "optimism"]


def test_env_override_scalar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("L2ARB__MIN_PROFIT_BPS", "25")
    assert Settings().min_profit_bps == 25


def test_env_nested_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("L2ARB__CHAINS__ARBITRUM__HTTP", "https://x,https://y")
    monkeypatch.setenv("L2ARB__CHAINS__ARBITRUM__WSS", "wss://x")
    s = Settings()
    assert s.chains["arbitrum"].http_urls == ("https://x", "https://y")
    assert s.chains["arbitrum"].wss_urls == ("wss://x",)
    assert s.enabled_chains == ["arbitrum"]


@pytest.mark.parametrize("bad", [1, 9, 0])
def test_max_hops_out_of_range_rejected(bad: int) -> None:
    with pytest.raises(ValidationError):
        Settings(max_hops=bad)


def test_negative_min_profit_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(min_profit_bps=-1)


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
