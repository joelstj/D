//! M9 output exit criteria: the envelope schema, and a subscriber receiving a
//! well-formed snapshot + opportunities message over the WS sink.

use alloy_primitives::{address, b256, U256};
use futures::StreamExt;
use l2i_core::{
    response::Block, Blockstamp, DecU256, DetectResponse, Pool, PoolAddress, PoolKind, Token,
    V2State,
};
use l2i_output::{Envelope, EnvelopeKind, OutputSink, WsServerSink};
use serde_json::Value;
use std::collections::BTreeMap;
use std::time::Duration;
use tokio_tungstenite::connect_async;

fn sample_pool() -> Pool {
    Pool {
        address: PoolAddress::Contract(address!("1111111111111111111111111111111111111111")),
        kind: PoolKind::V2,
        fee_pips: 3000,
        verified: true,
        token0: Token::with_symbol(
            42161,
            address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
            18,
            "WETH",
        ),
        token1: Token::with_symbol(
            42161,
            address!("af88d065e77c8cC2239327C5EDb3A432268e5831"),
            6,
            "USDC",
        ),
        blockstamp: Blockstamp {
            chain_id: 42161,
            number: 200_000_000,
            block_hash: b256!("fbb039d0d0e358b4d65f3df3058026fe5576beee3ed1fa2c1ad677d2efe0f3c1"),
            timestamp: 1_712_862_552,
        },
        v2: Some(V2State {
            reserve0: DecU256(U256::from(1000u64)),
            reserve1: DecU256(U256::from(2000u64)),
        }),
        v3: None,
    }
}

#[test]
fn snapshot_envelope_has_documented_shape() {
    let env = Envelope::snapshot(&[sample_pool()]).unwrap();
    let v: Value = serde_json::from_str(&env.to_ndjson().unwrap()).unwrap();
    assert_eq!(v["schema_version"], l2i_core::SCHEMA_VERSION);
    assert_eq!(v["kind"], "snapshot");
    // chain_blocks maps chain_id -> freshest block.
    assert_eq!(v["chain_blocks"]["42161"], 200_000_000u64);
    assert!(v["payload"].is_array());
    assert_eq!(v["payload"][0]["kind"], "v2");
    // Round-trips back into a typed Envelope.
    let back: Envelope = serde_json::from_value(v).unwrap();
    assert_eq!(back.kind, EnvelopeKind::Snapshot);
}

#[tokio::test(flavor = "multi_thread")]
async fn ws_subscriber_receives_snapshot_and_opportunities() {
    let sink = WsServerSink::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", sink.local_addr());

    let (mut ws, _resp) = connect_async(&url).await.unwrap();

    // Wait until the server has registered our subscription.
    for _ in 0..100 {
        if sink.subscriber_count() >= 1 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(sink.subscriber_count() >= 1, "subscriber not registered");

    // Publish a snapshot, then an opportunities envelope.
    sink.publish(&Envelope::snapshot(&[sample_pool()]).unwrap())
        .await
        .unwrap();
    let mut chain_blocks = BTreeMap::new();
    chain_blocks.insert(42161u64, 200_000_000u64);
    let resp = DetectResponse {
        count: 1,
        opportunities: vec![opp()],
    };
    sink.publish(&Envelope::opportunities(&resp, chain_blocks).unwrap())
        .await
        .unwrap();

    // Receive both, in order.
    let first = next_text(&mut ws).await;
    let v1: Value = serde_json::from_str(&first).unwrap();
    assert_eq!(v1["kind"], "snapshot");
    assert_eq!(v1["schema_version"], l2i_core::SCHEMA_VERSION);

    let second = next_text(&mut ws).await;
    let v2: Value = serde_json::from_str(&second).unwrap();
    assert_eq!(v2["kind"], "opportunities");
    assert_eq!(v2["payload"]["count"], 1);
}

async fn next_text<S>(ws: &mut S) -> String
where
    S: futures::Stream<
            Item = Result<
                tokio_tungstenite::tungstenite::Message,
                tokio_tungstenite::tungstenite::Error,
            >,
        > + Unpin,
{
    let msg = tokio::time::timeout(Duration::from_secs(5), ws.next())
        .await
        .expect("recv timed out")
        .expect("stream ended")
        .expect("ws error");
    match msg {
        tokio_tungstenite::tungstenite::Message::Text(t) => t.to_string(),
        other => panic!("expected text, got {other:?}"),
    }
}

fn opp() -> l2i_core::Opportunity {
    let t = Token::with_symbol(
        42161,
        address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
        18,
        "WETH",
    );
    l2i_core::Opportunity {
        strategy: "two_hop".into(),
        numeraire: t.clone(),
        input_amount: DecU256(U256::from(1u64)),
        output_amount: DecU256(U256::from(2u64)),
        gross_profit: DecU256(U256::from(1u64)),
        gas_cost: DecU256::ZERO,
        bridge_cost: DecU256::ZERO,
        net_profit: DecU256(U256::from(1u64)),
        profit_bps: 1.0,
        expected_net: DecU256(U256::from(1u64)),
        score: 1.0,
        hops: 2,
        chain_ids: vec![42161],
        is_cross_chain: false,
        settle_seconds: 0,
        verified: true,
        block: Block {
            chain_id: 42161,
            number: 200_000_000,
            hash: b256!("fbb039d0d0e358b4d65f3df3058026fe5576beee3ed1fa2c1ad677d2efe0f3c1"),
            timestamp: 1,
        },
        risk: l2i_core::Risk {
            success_probability: 0.9,
            capture_ratio: 0.6,
            frontrun_risk: 0.1,
            notes: vec![],
        },
        legs: vec![],
    }
}
