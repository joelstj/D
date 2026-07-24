//! Golden (de)serialization tests for the engine contract.
//!
//! IMPORTANT — the values here are the engine contract's **illustrative example**
//! (`docs/reference/INTEGRATION.md §3–4`), used *solely* to prove our JSON shape is
//! byte-for-byte what the engine documented. This is explicitly what `BUILD_PLAN.md
//! → M0` asks for ("a fixture that mirrors the contract's example"). It is **not** an
//! on-chain equality test and asserts nothing about the `verified` semantics — real,
//! captured-on-chain pool state and the `verified` proof live in M2+ fixtures. Token
//! addresses and the blockstamp are real public constants (Arbitrum WETH/USDC, real
//! block 200000000); the reserves and pool addresses are the contract's illustrative
//! placeholders. No shipped code path uses these numbers.
//!
//! Fixtures are regenerated with `REGEN_FIXTURES=1 cargo test -p l2i-core`, then
//! reviewed and committed; the locked test guards serialization stability forever.

use alloy_primitives::{address, b256, Address, B256};
use l2i_core::{
    cross_chain::{Asset, Bridge, CrossChain, Representation},
    pool::{Blockstamp, Pool, PoolAddress, PoolKind, V2State},
    request::{ChainContext, DetectRequest},
    response::{Block, DetectResponse, Leg, Opportunity, Risk},
    token::Token,
    DecU256,
};
use std::collections::BTreeMap;
use std::path::PathBuf;

// Real, public token constants (Arbitrum One).
const WETH: Address = address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1");
const USDC: Address = address!("af88d065e77c8cC2239327C5EDb3A432268e5831");
// Real Arbitrum block 200000000 (header captured on-chain).
const BLOCK_HASH: B256 = b256!("fbb039d0d0e358b4d65f3df3058026fe5576beee3ed1fa2c1ad677d2efe0f3c1");
const BLOCK_NUMBER: u64 = 200_000_000;
const BLOCK_TS: u64 = 1_712_862_552;
// Illustrative pool identities (contract-example placeholders; not live pools).
const POOL: Address = address!("1111111111111111111111111111111111111111");
const LEG1_POOL: Address = address!("2222222222222222222222222222222222222222");
const LEG2_POOL: Address = address!("3333333333333333333333333333333333333333");

fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

/// Assert `got` equals the checked-in fixture (byte-for-byte, trailing newline
/// aside). With `REGEN_FIXTURES=1`, (re)write the fixture instead.
fn assert_golden(name: &str, got: &str) {
    let path = fixture_path(name);
    if std::env::var_os("REGEN_FIXTURES").is_some() {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, format!("{got}\n")).unwrap();
        eprintln!("regenerated {}", path.display());
        return;
    }
    let want = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "missing fixture {} ({e}); run REGEN_FIXTURES=1",
            path.display()
        )
    });
    assert_eq!(
        got,
        want.trim_end_matches('\n'),
        "golden mismatch for {name}"
    );
}

fn weth() -> Token {
    Token::with_symbol(42161, WETH, 18, "WETH")
}
fn usdc() -> Token {
    Token::with_symbol(42161, USDC, 6, "USDC")
}

/// The engine contract's example `DetectRequest` (§3).
fn example_request() -> DetectRequest {
    let mut native_price_in = BTreeMap::new();
    native_price_in.insert(WETH, 1.0);
    native_price_in.insert(USDC, 0.0000000003);

    let chain = ChainContext {
        chain_id: 42161,
        gas_price_wei: 10_000_000,
        l1_data_fee_wei: 0,
        base_gas: 150_000,
        per_hop_gas: 100_000,
        gas_safety_multiplier: 1.5,
        min_profit_bps: 5.0,
        native_price_in,
        hubs: vec![WETH, USDC],
    };

    // token0.address < token1.address byte-wise: WETH (0x82…) < USDC (0xaf…).
    let pool = Pool {
        address: PoolAddress::Contract(POOL),
        kind: PoolKind::V2,
        fee_pips: 3000,
        verified: true,
        token0: weth(),
        token1: usdc(),
        blockstamp: Blockstamp {
            chain_id: 42161,
            number: BLOCK_NUMBER,
            block_hash: BLOCK_HASH,
            timestamp: BLOCK_TS,
        },
        v2: Some(V2State {
            reserve0: DecU256::from(1_234_567_890_000_000_000_000u128),
            reserve1: DecU256::from(3_210_000_000_000u128),
        }),
        v3: None,
    };

    let cross_chain = CrossChain {
        assets: vec![
            Asset {
                symbol: "WETH".into(),
                representations: vec![
                    Representation {
                        token: Token::bare(42161, WETH, 18),
                        native: true,
                        bridgeable: true,
                    },
                    Representation {
                        token: Token::bare(
                            8453,
                            address!("4200000000000000000000000000000000000006"),
                            18,
                        ),
                        native: true,
                        bridgeable: true,
                    },
                ],
            },
            Asset {
                symbol: "USDC".into(),
                representations: vec![Representation {
                    token: Token::bare(42161, USDC, 6),
                    native: true,
                    bridgeable: true,
                }],
            },
        ],
        bridges: vec![Bridge {
            symbol: "WETH".into(),
            from_chain: 42161,
            to_chain: 8453,
            fee_bps: 10.0,
            fixed_fee: 0,
            settle_seconds: 600,
        }],
        pairs: vec![["WETH".into(), "USDC".into()]],
    };

    DetectRequest {
        top_n: 10,
        max_hops: 4,
        incremental: false,
        chains: vec![chain],
        pools: vec![pool],
        cross_chain: Some(cross_chain),
    }
}

/// A response mirroring the engine contract's example (§4), with concrete,
/// internally-consistent amounts in place of the doc's `"…"` placeholders.
fn example_response() -> DetectResponse {
    let opp = Opportunity {
        strategy: "two_hop".into(),
        numeraire: weth(),
        input_amount: DecU256::from(1_000_000_000_000_000_000u128), // 1 WETH
        output_amount: DecU256::from(1_045_000_000_000_000_000u128), // 1.045 WETH
        gross_profit: DecU256::from(45_000_000_000_000_000u128),    // 0.045
        gas_cost: DecU256::from(5_000_000_000_000_000u128),         // 0.005
        bridge_cost: DecU256::ZERO,
        net_profit: DecU256::from(40_000_000_000_000_000u128), // 0.045 - 0.005
        profit_bps: 400.0,
        expected_net: DecU256::from(24_000_000_000_000_000u128), // net × capture_ratio
        score: 24_000_000_000_000_000.0,
        hops: 2,
        chain_ids: vec![42161],
        is_cross_chain: false,
        settle_seconds: 0,
        verified: true,
        block: Block {
            chain_id: 42161,
            number: BLOCK_NUMBER,
            hash: BLOCK_HASH,
            timestamp: BLOCK_TS,
        },
        risk: Risk {
            success_probability: 0.9,
            capture_ratio: 0.6,
            frontrun_risk: 0.1,
            notes: vec!["hops=2".into(), "same-chain".into()],
        },
        legs: vec![
            Leg {
                pool: PoolAddress::Contract(LEG1_POOL),
                token_in: weth(),
                token_out: usdc(),
                amount_in: DecU256::from(1_000_000_000_000_000_000u128),
                amount_out: DecU256::from(1_873_000_000u128), // 1873 USDC
            },
            Leg {
                pool: PoolAddress::Contract(LEG2_POOL),
                token_in: usdc(),
                token_out: weth(),
                amount_in: DecU256::from(1_873_000_000u128),
                amount_out: DecU256::from(1_045_000_000_000_000_000u128),
            },
        ],
    };
    DetectResponse {
        count: 1,
        opportunities: vec![opp],
    }
}

#[test]
fn golden_detect_request_serializes_byte_stable() {
    let got = serde_json::to_string_pretty(&example_request()).unwrap();
    assert_golden("detect_request.pretty.json", &got);
}

#[test]
fn golden_detect_response_serializes_byte_stable() {
    let got = serde_json::to_string_pretty(&example_response()).unwrap();
    assert_golden("detect_response.pretty.json", &got);
}

#[test]
fn deserialize_contract_response_fixture() {
    let raw = std::fs::read_to_string(fixture_path("detect_response.pretty.json")).unwrap();
    let resp = DetectResponse::from_engine_json(&raw).unwrap();
    assert_eq!(resp.count as usize, resp.opportunities.len());
    let opp = &resp.opportunities[0];
    assert!(
        opp.net_profit.get() > alloy_primitives::U256::ZERO,
        "reported opp must have net_profit > 0"
    );
    assert_eq!(opp.block.hash, BLOCK_HASH, "blockstamp must round-trip");
    assert_eq!(opp.legs.len(), 2);
    assert_eq!(opp.strategy, "two_hop");
}

#[test]
fn request_roundtrips_through_json() {
    let req = example_request();
    let json = req.to_engine_json().unwrap();
    // Compact form is a single line (no pretty indentation) for the hot path.
    assert!(!json.contains('\n'));
    let back: DetectRequest = serde_json::from_str(&json).unwrap();
    assert_eq!(req, back);
}

#[test]
fn response_roundtrips_through_json() {
    let resp = example_response();
    let json = serde_json::to_string(&resp).unwrap();
    let back = DetectResponse::from_engine_json(&json).unwrap();
    assert_eq!(resp, back);
}

#[test]
fn v4_poolid_identity_roundtrips() {
    // A V4 poolId (32 bytes) survives as the pool `address`, told apart from a
    // 20-byte contract address by length.
    let id = b256!("aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899");
    let pa = PoolAddress::PoolId(id);
    let json = serde_json::to_string(&pa).unwrap();
    assert_eq!(json, format!("\"{id}\""));
    let back: PoolAddress = serde_json::from_str(&json).unwrap();
    assert_eq!(pa, back);
    assert_eq!(back.pool_id(), Some(id));
    assert_eq!(back.contract(), None);
}

#[test]
fn pool_address_rejects_wrong_length() {
    assert!(serde_json::from_str::<PoolAddress>("\"0x1234\"").is_err());
}

#[test]
fn engine_error_shape_parses() {
    let raw = r#"{"error":"bad request","type":"ValidationError"}"#;
    let e: l2i_core::EngineError = serde_json::from_str(raw).unwrap();
    assert_eq!(e.error_type, "ValidationError");
}
