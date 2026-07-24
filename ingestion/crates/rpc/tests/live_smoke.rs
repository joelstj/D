//! Tier-B live smoke test (M1 exit criterion).
//!
//! Connects to each of the five chains and asserts basic liveness: a block height,
//! Multicall3 code present at the canonical address, and a working `aggregate3`.
//! This is **Tier B** — it needs live endpoints, so it runs only when `L2I_LIVE=1`
//! and is a no-op otherwise (CI's deterministic Tier-A gate never depends on it).
//! Endpoints default to public HTTP RPCs; override per chain with
//! `L2I_HTTP_<chain_id>` and enable subscriptions with `L2I_WS_<chain_id>`.

use alloy::rpc::types::eth::BlockId;
use l2i_rpc::multicall::{require_all, Call3};
use l2i_rpc::provider::{AlloyProvider, ChainProvider};
use std::time::Duration;

const CHAINS: &[(u64, &str, &str)] = &[
    (42161, "arbitrum", "https://arb1.arbitrum.io/rpc"),
    (8453, "base", "https://mainnet.base.org"),
    (10, "optimism", "https://mainnet.optimism.io"),
    (130, "unichain", "https://mainnet.unichain.org"),
    (57073, "ink", "https://rpc-gel.inkonchain.com"),
];

fn http_url(chain_id: u64, default: &str) -> String {
    std::env::var(format!("L2I_HTTP_{chain_id}")).unwrap_or_else(|_| default.to_string())
}

#[tokio::test(flavor = "multi_thread")]
async fn live_smoke_all_five_chains() {
    if std::env::var("L2I_LIVE").ok().as_deref() != Some("1") {
        eprintln!("SKIP live_smoke: set L2I_LIVE=1 to run the Tier-B live smoke test");
        return;
    }

    for &(chain_id, name, default_http) in CHAINS {
        let http = http_url(chain_id, default_http);
        let ws = std::env::var(format!("L2I_WS_{chain_id}")).ok();

        let provider = tokio::time::timeout(
            Duration::from_secs(20),
            AlloyProvider::connect(chain_id, &http, ws.as_deref()),
        )
        .await
        .unwrap_or_else(|_| panic!("{name}: connect timed out"))
        .unwrap_or_else(|e| panic!("{name}: connect failed: {e}"));

        // 1) A live block height.
        let bn = provider.block_number().await.expect("block_number");
        assert!(bn > 0, "{name}: block number should be > 0");

        // 2) Multicall3 code present at the canonical address.
        let code = provider
            .code_at(l2i_chains::MULTICALL3, BlockId::latest())
            .await
            .expect("code_at multicall3");
        assert!(
            !code.is_empty(),
            "{name}: Multicall3 must be deployed at {}",
            l2i_chains::MULTICALL3
        );

        // 3) A working aggregate3 (getBlockNumber sub-call must succeed).
        let calls = vec![Call3::required(
            l2i_chains::MULTICALL3,
            alloy_primitives::Bytes::from_static(&[0x42, 0xcb, 0xb1, 0x5c]),
        )];
        let results = provider
            .multicall(calls, BlockId::latest())
            .await
            .expect("multicall");
        let blobs = require_all(results).expect("aggregate3 sub-call succeeded");
        assert_eq!(blobs.len(), 1);

        // 4) A head summary round-trips.
        let head = provider.head(BlockId::latest()).await.expect("head");
        assert!(head.number > 0);

        eprintln!("OK  {name:<9} chain_id={chain_id:<6} block={bn} multicall3=present");
    }
}
