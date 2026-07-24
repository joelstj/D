//! Multicall3 encode/decode proven against a **recorded real** on-chain response.
//!
//! The fixture (`fixtures/multicall3_aggregate3_arbitrum.json`) is a genuine
//! `aggregate3` response captured from Arbitrum One at a pinned block via the
//! documented capture step. This test proves both directions:
//!   - our encoder reproduces the exact request calldata, and
//!   - our decoder reproduces the exact sub-results the node returned.

use alloy_primitives::{hex, Bytes, U256};
use l2i_rpc::multicall::{decode_aggregate3, encode_aggregate3, Call3};
use serde_json::Value;

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/multicall3_aggregate3_arbitrum.json"
    ))
    .expect("fixture present");
    serde_json::from_str(&raw).expect("valid fixture JSON")
}

#[test]
fn encoder_reproduces_recorded_calldata() {
    let fx = load_fixture();
    let mc = l2i_chains::MULTICALL3;
    let calls = vec![
        // getCurrentBlockTimestamp(), getBlockNumber() — Multicall3's own helpers.
        Call3::required(mc, Bytes::from_static(&[0x0f, 0x28, 0xc9, 0x7d])),
        Call3::required(mc, Bytes::from_static(&[0x42, 0xcb, 0xb1, 0x5c])),
    ];
    let got = encode_aggregate3(calls);
    let want = fx["calldata"].as_str().unwrap();
    assert_eq!(format!("0x{}", hex::encode(&got)), want);
}

#[test]
fn decoder_reproduces_recorded_results() {
    let fx = load_fixture();
    let resp_hex = fx["response"].as_str().unwrap();
    let bytes = hex::decode(resp_hex.trim_start_matches("0x")).unwrap();

    let results = decode_aggregate3(&bytes).expect("decode recorded response");
    let expected = fx["expected"]["results"].as_array().unwrap();
    assert_eq!(results.len(), expected.len());

    for (got, want) in results.iter().zip(expected) {
        assert_eq!(got.success, want["success"].as_bool().unwrap());
        let got_u = U256::from_be_slice(&got.returnData);
        let want_u = U256::from_str_radix(want["return_uint"].as_str().unwrap(), 10).unwrap();
        assert_eq!(got_u, want_u);
    }
}
