//! AMM known-answer tests against **recorded real** pinned-block data, plus the
//! on-chain tick↔sqrtPrice invariant.
//!
//! `fixtures/arbitrum_amm.json` holds a real Camelot V2 WETH/USDC reserve snapshot
//! and a real Uniswap V3 WETH/USDC `slot0`, each with expected values computed
//! independently (Python big-int) at capture time. Our Rust math must reproduce
//! them exactly (V2) or within f64 tolerance (V3 price).

use alloy_primitives::U256;
use l2i_amm::native::native_price_in;
use l2i_amm::v2::get_amount_out;
use l2i_amm::v3::{get_sqrt_ratio_at_tick, sqrt_price_x96_to_price};
use serde_json::Value;

fn fixture() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/arbitrum_amm.json"
    ))
    .unwrap();
    serde_json::from_str(&raw).unwrap()
}

fn u256(s: &str) -> U256 {
    U256::from_str_radix(s, 10).unwrap()
}

#[test]
fn v2_get_amount_out_matches_real_reserves() {
    let fx = fixture();
    let v2 = &fx["v2"];
    let (r0, r1) = (
        u256(v2["reserve0"].as_str().unwrap()),
        u256(v2["reserve1"].as_str().unwrap()),
    );
    let weth_is_token0 = v2["weth_is_token0"].as_bool().unwrap();
    let (rin, rout) = if weth_is_token0 { (r0, r1) } else { (r1, r0) };
    let amount_in = u256(v2["amount_in_weth"].as_str().unwrap());
    let fee = v2["fee_pips"].as_u64().unwrap() as u32;
    let got = get_amount_out(amount_in, rin, rout, fee);
    let want = u256(v2["expected_out_usdc"].as_str().unwrap());
    assert_eq!(
        got, want,
        "V2 getAmountOut must match the independently-computed value exactly"
    );
}

#[test]
fn v3_price_matches_real_slot0() {
    let fx = fixture();
    let v3 = &fx["v3"];
    let sqrt = u256(v3["sqrt_price_x96"].as_str().unwrap());
    let (dec0, dec1) = (
        v3["dec0"].as_i64().unwrap() as i32,
        v3["dec1"].as_i64().unwrap() as i32,
    );
    let price = sqrt_price_x96_to_price(sqrt, dec0, dec1);
    let want = v3["expected_price_weth_in_usdc"].as_f64().unwrap();
    let rel = (price - want).abs() / want;
    assert!(
        rel < 1e-9,
        "V3 price {price} vs expected {want} (rel {rel})"
    );
    // And the implied ETH price is realistic (proves the data is real).
    assert!(
        (100.0..100_000.0).contains(&price),
        "implausible ETH price {price}"
    );

    let npi = native_price_in(price, dec1 as u8);
    let want_npi = v3["expected_native_price_in_usdc"].as_f64().unwrap();
    assert!(
        (npi - want_npi).abs() / want_npi < 1e-9,
        "native_price_in {npi} vs {want_npi}"
    );
}

#[test]
fn tick_and_sqrt_price_are_consistent_on_chain() {
    // The on-chain slot0 invariant: getSqrtRatioAtTick(tick) <= sqrtP <
    // getSqrtRatioAtTick(tick+1). This proves our tick math matches Uniswap's,
    // cross-checked against a real pool.
    let fx = fixture();
    let v3 = &fx["v3"];
    let sqrt = u256(v3["sqrt_price_x96"].as_str().unwrap());
    let tick = v3["tick"].as_i64().unwrap() as i32;
    let lo = get_sqrt_ratio_at_tick(tick).unwrap();
    let hi = get_sqrt_ratio_at_tick(tick + 1).unwrap();
    assert!(lo <= sqrt, "getSqrtRatioAtTick({tick})={lo} > sqrtP={sqrt}");
    assert!(
        sqrt < hi,
        "sqrtP={sqrt} >= getSqrtRatioAtTick({})={hi}",
        tick + 1
    );
}
