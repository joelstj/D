//! M8 engine-client contract conformance.
//!
//! The real `l2arb` engine is not available in this environment (not published to
//! PyPI), so the "real engine returns net_profit>0 for a known arb" criterion is
//! **BLOCKED** and recorded as such in `ralph/PROGRESS.md` — never faked. What we
//! *can* prove here is that **our client conforms to the documented contract**
//! (`docs/reference/INTEGRATION.md`): against a mock server / subprocess that speaks
//! the documented request/response shapes, `detect`/`health` work, blockstamps
//! round-trip, `validate_response` passes on a good response and flags a bad one,
//! and the documented error shape is surfaced.

use alloy_primitives::{address, b256, Address, B256, U256};
use l2i_core::{
    response::{Block, Leg, Opportunity, Risk},
    Blockstamp, ChainContext, DecU256, DetectRequest, DetectResponse, Pool, PoolAddress, PoolKind,
    Token, V2State,
};
use l2i_engine_client::{
    validate_response, EngineClient, HttpConfig, HttpEngineClient, SubprocessEngineClient,
};
use std::collections::BTreeMap;
use std::time::Duration;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const POOL: Address = address!("1111111111111111111111111111111111111111");
const WETH: Address = address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1");
const USDC: Address = address!("af88d065e77c8cC2239327C5EDb3A432268e5831");
const HASH: B256 = b256!("fbb039d0d0e358b4d65f3df3058026fe5576beee3ed1fa2c1ad677d2efe0f3c1");
const NUMBER: u64 = 200_000_000;

fn weth() -> Token {
    Token::with_symbol(42161, WETH, 18, "WETH")
}
fn usdc() -> Token {
    Token::with_symbol(42161, USDC, 6, "USDC")
}
fn stamp() -> Blockstamp {
    Blockstamp {
        chain_id: 42161,
        number: NUMBER,
        block_hash: HASH,
        timestamp: 1_712_862_552,
    }
}

fn example_request() -> DetectRequest {
    let pool = Pool {
        address: PoolAddress::Contract(POOL),
        kind: PoolKind::V2,
        fee_pips: 3000,
        verified: true,
        token0: weth(),
        token1: usdc(),
        blockstamp: stamp(),
        v2: Some(V2State {
            reserve0: DecU256(U256::from(1_000u64)),
            reserve1: DecU256(U256::from(2_000u64)),
        }),
        v3: None,
    };
    let ctx = ChainContext {
        chain_id: 42161,
        gas_price_wei: 10_000_000,
        l1_data_fee_wei: 0,
        base_gas: 150_000,
        per_hop_gas: 100_000,
        gas_safety_multiplier: 1.5,
        min_profit_bps: 5.0,
        native_price_in: BTreeMap::new(),
        hubs: vec![],
    };
    DetectRequest {
        top_n: 10,
        max_hops: 4,
        incremental: false,
        chains: vec![ctx],
        pools: vec![pool],
        cross_chain: None,
    }
}

fn example_response() -> DetectResponse {
    // A schema-valid response referencing the request's pool + blockstamp, with
    // net_profit > 0 (documented-contract shape; NOT a real l2arb computation).
    let opp = Opportunity {
        strategy: "two_hop".into(),
        numeraire: weth(),
        input_amount: DecU256(U256::from(1_000_000_000_000_000_000u128)),
        output_amount: DecU256(U256::from(1_040_000_000_000_000_000u128)),
        gross_profit: DecU256(U256::from(40_000_000_000_000_000u128)),
        gas_cost: DecU256(U256::from(5_000_000_000_000_000u128)),
        bridge_cost: DecU256::ZERO,
        net_profit: DecU256(U256::from(35_000_000_000_000_000u128)),
        profit_bps: 350.0,
        expected_net: DecU256(U256::from(21_000_000_000_000_000u128)),
        score: 2.1e16,
        hops: 2,
        chain_ids: vec![42161],
        is_cross_chain: false,
        settle_seconds: 0,
        verified: true,
        block: Block {
            chain_id: 42161,
            number: NUMBER,
            hash: HASH,
            timestamp: 1_712_862_552,
        },
        risk: Risk {
            success_probability: 0.9,
            capture_ratio: 0.6,
            frontrun_risk: 0.1,
            notes: vec!["hops=2".into()],
        },
        legs: vec![Leg {
            pool: PoolAddress::Contract(POOL),
            token_in: weth(),
            token_out: usdc(),
            amount_in: DecU256(U256::from(1_000_000_000_000_000_000u128)),
            amount_out: DecU256(U256::from(1_873_000_000u64)),
        }],
    };
    DetectResponse {
        count: 1,
        opportunities: vec![opp],
        timing: None,
    }
}

#[tokio::test]
async fn http_health_detect_and_validation() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"status":"ok"})))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/detect"))
        .respond_with(ResponseTemplate::new(200).set_body_json(example_response()))
        .mount(&server)
        .await;

    let client = HttpEngineClient::new(HttpConfig {
        base_url: server.uri(),
        timeout: Duration::from_secs(5),
        ..Default::default()
    })
    .unwrap();

    assert!(client.health().await.unwrap(), "health should be ok");

    let req = example_request();
    let resp = client.detect(&req).await.unwrap();
    assert_eq!(resp.count, 1);
    // Blockstamp round-trips.
    assert_eq!(resp.opportunities[0].block.hash, HASH);
    // Response passes all §10 checks against the request we sent.
    let issues = validate_response(&req, &resp);
    assert!(issues.is_empty(), "unexpected issues: {issues:?}");
}

#[tokio::test]
async fn http_error_shape_is_surfaced() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/detect"))
        .respond_with(
            ResponseTemplate::new(400).set_body_json(
                serde_json::json!({"error":"bad max_hops","type":"ValidationError"}),
            ),
        )
        .mount(&server)
        .await;
    let client = HttpEngineClient::new(HttpConfig {
        base_url: server.uri(),
        timeout: Duration::from_secs(5),
        ..Default::default()
    })
    .unwrap();
    let err = client.detect(&example_request()).await.unwrap_err();
    let msg = err.to_string();
    assert!(msg.contains("ValidationError"), "got: {msg}");
}

#[test]
fn validate_response_flags_bad_responses() {
    let req = example_request();

    // net_profit == 0 → flagged.
    let mut bad = example_response();
    bad.opportunities[0].net_profit = DecU256::ZERO;
    let issues = validate_response(&req, &bad);
    assert!(issues.iter().any(|i| matches!(
        i,
        l2i_engine_client::ResponseIssue::NonPositiveProfit { .. }
    )));

    // Wrong blockstamp → flagged.
    let mut bad2 = example_response();
    bad2.opportunities[0].block.number = 999;
    let issues2 = validate_response(&req, &bad2);
    assert!(issues2.iter().any(|i| matches!(
        i,
        l2i_engine_client::ResponseIssue::BlockstampNotInRequest { .. }
    )));

    // A leg referencing an unverified/unknown pool → flagged.
    let mut bad3 = example_response();
    bad3.opportunities[0].legs[0].pool =
        PoolAddress::Contract(address!("dead00000000000000000000000000000000beef"));
    let issues3 = validate_response(&req, &bad3);
    assert!(issues3.iter().any(|i| matches!(
        i,
        l2i_engine_client::ResponseIssue::LegPoolNotVerified { .. }
    )));
}

#[tokio::test]
async fn subprocess_transport_parses_response_and_error() {
    // A stand-in "engine": ignore stdin, print a fixed documented-shape response.
    let resp_json = serde_json::to_string(&example_response()).unwrap();
    let ok_script = format!(
        "cat >/dev/null; printf %s '{}'",
        resp_json.replace('\'', "'\\''")
    );
    let client =
        SubprocessEngineClient::from_parts("sh", ["-c", &ok_script], Duration::from_secs(5));
    let resp = client.detect(&example_request()).await.unwrap();
    assert_eq!(resp.count, 1);
    assert!(validate_response(&example_request(), &resp).is_empty());

    // An "engine" that fails with the documented error shape on stdout, exit 1.
    let err_script = r#"cat >/dev/null; printf %s '{"error":"boom","type":"BadRequest"}'; exit 1"#;
    let client =
        SubprocessEngineClient::from_parts("sh", ["-c", err_script], Duration::from_secs(5));
    let err = client.detect(&example_request()).await.unwrap_err();
    assert!(err.to_string().contains("BadRequest"), "got: {err}");
}
