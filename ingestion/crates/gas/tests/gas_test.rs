//! M7 exit criteria over **recorded real** pinned-block gas reads.
//!
//! `fixtures/gas.json`: real `eth_gasPrice` on Base and Arbitrum, and a real
//! OP-Stack `GasPriceOracle.getL1Fee(sample_tx)` on Base, each at a pinned block.
//! Our adapters must reproduce the same values; `l1_data_fee_wei` for Arbitrum is
//! `0` (L1 cost folded into gas units). A numeraire with no native-price path is
//! omitted from the assembled `ChainContext`.

use alloy_primitives::{address, hex, Address, Bytes, U256};
use l2i_chains::GasModel;
use l2i_gas::{
    assemble_chain_context, build_native_price_map, getl1fee_calldata, read_gas_price,
    read_l1_data_fee, GasConfig,
};
use l2i_rpc::mock::MockProvider;
use l2i_rpc::BlockId;
use serde_json::Value;

fn fixture() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/gas.json"
    ))
    .unwrap();
    serde_json::from_str(&raw).unwrap()
}

#[tokio::test]
async fn base_gas_price_and_l1_fee_match_chain() {
    let fx = fixture();
    let base = &fx["chains"]["base"];
    let oracle: Address = fx["op_gas_price_oracle"].as_str().unwrap().parse().unwrap();
    let sample = Bytes::from(hex::decode(base["sample_tx"].as_str().unwrap()).unwrap());
    let block = base["block"].as_u64().unwrap();

    // The encoder reproduces the recorded getL1Fee calldata exactly.
    let cd = getl1fee_calldata(sample.clone());
    assert_eq!(
        format!("0x{}", hex::encode(&cd)),
        base["getl1fee_calldata"].as_str().unwrap()
    );

    // The getL1Fee return is the canonical uint256 encoding of the real value.
    let l1 = base["l1_data_fee_wei"].as_u64().unwrap();
    let response = Bytes::from(U256::from(l1).to_be_bytes::<32>().to_vec());

    let mock = MockProvider::new(8453)
        .with_gas_price(base["gas_price_wei"].as_u64().unwrap())
        .with_call(oracle, cd, response);

    assert_eq!(
        read_gas_price(&mock).await.unwrap(),
        base["gas_price_wei"].as_u64().unwrap()
    );
    let got_l1 = read_l1_data_fee(
        &mock,
        GasModel::OpStack,
        oracle,
        sample,
        BlockId::from(block),
    )
    .await
    .unwrap();
    assert_eq!(got_l1, l1, "getL1Fee must match the on-chain oracle read");
}

#[tokio::test]
async fn arbitrum_l1_fee_is_zero_and_gas_price_matches() {
    let fx = fixture();
    let arb = &fx["chains"]["arbitrum"];
    let mock = MockProvider::new(42161).with_gas_price(arb["gas_price_wei"].as_u64().unwrap());
    assert_eq!(
        read_gas_price(&mock).await.unwrap(),
        arb["gas_price_wei"].as_u64().unwrap()
    );
    // Arbitrum folds L1 calldata cost into gas units → l1_data_fee_wei = 0.
    let l1 = read_l1_data_fee(
        &mock,
        GasModel::Arbitrum,
        Address::ZERO,
        Bytes::new(),
        BlockId::latest(),
    )
    .await
    .unwrap();
    assert_eq!(l1, 0);
    assert_eq!(l1, arb["l1_data_fee_wei"].as_u64().unwrap());
}

#[test]
fn chain_context_omits_numeraire_without_price_path() {
    let weth = address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1");
    let usdc = address!("af88d065e77c8cC2239327C5EDb3A432268e5831");
    let dai = address!("DA10009cBd5D07dd0CeCc66161FC93D7c9000da1"); // no derivable path
    let native_price_in = build_native_price_map(weth, [(usdc, Some(1.9e-9)), (dai, None)]);

    let cfg = GasConfig {
        base_gas: 150_000,
        per_hop_gas: 100_000,
        gas_safety_multiplier: 1.5,
        min_profit_bps: 5.0,
    };
    let ctx = assemble_chain_context(
        8453,
        6_000_000,
        567_920_048,
        cfg,
        native_price_in,
        vec![weth, usdc],
    );

    assert_eq!(ctx.chain_id, 8453);
    assert_eq!(ctx.gas_price_wei, 6_000_000);
    assert_eq!(ctx.l1_data_fee_wei, 567_920_048);
    assert!(ctx.native_price_in.contains_key(&weth));
    assert!(ctx.native_price_in.contains_key(&usdc));
    assert!(
        !ctx.native_price_in.contains_key(&dai),
        "no-path numeraire must be omitted"
    );
    assert_eq!(*ctx.native_price_in.get(&weth).unwrap(), 1.0);
}
