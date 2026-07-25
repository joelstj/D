//! [`PrefetchProvider`] — batch once, then replay reads with no network.
//!
//! Boot-time work that does many independent `eth_call`s to fixed targets (above
//! all the **validation gate**, which reads `token0`/`token1`/`fee`/`factory`/
//! `decimals`/`symbol` for every configured pool) used to issue one request per
//! read. Instead we pre-fetch **all** those reads in a handful of batched round-trips
//! — a single `eth_getCode` JSON-RPC batch plus chunked Multicall3 `aggregate3`
//! calls — and hand back a `ChainProvider` that answers each individual `call`/
//! `code_at` from the captured results. The caller then runs its *existing*
//! per-item logic (e.g. [`crate::multicall`]-unaware `validate_pool`) against this
//! provider unchanged, so behaviour is identical but the request count collapses
//! from O(pools) to O(1).
//!
//! It is a pure read cache built from **real** on-chain reads captured at `block`:
//! nothing is invented. A sub-call that reverted on-chain is simply absent, so a
//! replayed `call` for it errors — exactly as the live `eth_call` would have.

use crate::error::{Result, RpcError};
use crate::frame::HeadSummary;
use crate::multicall::Call3;
use crate::provider::{ChainProvider, HeadStream, LogStream};
use alloy::rpc::types::eth::BlockId;
use alloy::rpc::types::{Filter, Log};
use alloy_primitives::{Address, Bytes};
use async_trait::async_trait;
use std::collections::HashMap;

/// Max sub-calls per Multicall3 `aggregate3` batch. Bounds response size and node
/// gas so a large read set splits into a few batches rather than one giant call.
/// All reads still execute — this is chunking, never a cap.
pub const MULTICALL_CHUNK: usize = 500;

/// A [`ChainProvider`] that serves a fixed set of pre-captured reads with no network.
#[derive(Clone, Debug, Default)]
pub struct PrefetchProvider {
    chain_id: u64,
    /// `(to, calldata) → return bytes` for every sub-call that succeeded on-chain.
    calls: HashMap<(Address, Vec<u8>), Bytes>,
    /// `address → code`, or `Err(msg)` if the code read itself failed.
    code: HashMap<Address, std::result::Result<Bytes, String>>,
}

impl PrefetchProvider {
    /// Batch-fetch `code_addrs` (one `eth_getCode` batch) and `calls` (chunked
    /// Multicall3 `aggregate3`) from `provider` at `block`, then serve them offline.
    ///
    /// `calls` is the exact `(target, calldata)` set the caller's per-item logic will
    /// later request. Sub-calls are issued with `allowFailure`, so one reverting read
    /// never sinks the batch; a reverted read is omitted and a later replayed `call`
    /// for it errors, mirroring a live revert. If the whole code batch fails, it
    /// falls back to per-address reads so one bad endpoint response can't wrongly
    /// reject every pool.
    pub async fn fetch<P: ChainProvider + ?Sized>(
        provider: &P,
        code_addrs: &[Address],
        calls: &[(Address, Bytes)],
        block: BlockId,
    ) -> Self {
        let chain_id = provider.chain_id();

        let code = if code_addrs.is_empty() {
            HashMap::new()
        } else {
            match provider.code_at_batch(code_addrs, block).await {
                Ok(codes) => code_addrs
                    .iter()
                    .cloned()
                    .zip(codes.into_iter().map(Ok))
                    .collect(),
                Err(e) => {
                    tracing::warn!(error = %e, "batched eth_getCode failed; falling back to per-address");
                    let mut m = HashMap::with_capacity(code_addrs.len());
                    for a in code_addrs {
                        m.insert(
                            *a,
                            provider.code_at(*a, block).await.map_err(|e| e.to_string()),
                        );
                    }
                    m
                }
            }
        };

        let mut call_map: HashMap<(Address, Vec<u8>), Bytes> = HashMap::with_capacity(calls.len());
        for chunk in calls.chunks(MULTICALL_CHUNK) {
            let c3: Vec<Call3> = chunk
                .iter()
                .map(|(to, data)| Call3::allow_failure(*to, data.clone()))
                .collect();
            match provider.multicall(c3, block).await {
                Ok(results) => {
                    for ((to, data), r) in chunk.iter().zip(results) {
                        if r.success {
                            call_map.insert((*to, data.to_vec()), r.returnData);
                        }
                    }
                }
                Err(e) => {
                    // Leave this chunk's reads absent — the affected items degrade to
                    // an RPC rejection, never a silent wrong value.
                    tracing::warn!(error = %e, "prefetch multicall chunk failed");
                }
            }
        }

        Self {
            chain_id,
            calls: call_map,
            code,
        }
    }

    /// The number of distinct reads captured (for tests / diagnostics).
    pub fn captured_reads(&self) -> usize {
        self.calls.len() + self.code.len()
    }
}

const UNSUPPORTED: &str = "PrefetchProvider serves only replayed call/code_at reads";

#[async_trait]
impl ChainProvider for PrefetchProvider {
    fn chain_id(&self) -> u64 {
        self.chain_id
    }

    async fn block_number(&self) -> Result<u64> {
        Err(RpcError::Call(UNSUPPORTED.into()))
    }

    async fn gas_price(&self) -> Result<u64> {
        Err(RpcError::Call(UNSUPPORTED.into()))
    }

    async fn head(&self, _at: BlockId) -> Result<HeadSummary> {
        Err(RpcError::Call(UNSUPPORTED.into()))
    }

    async fn call(&self, to: Address, data: Bytes, _at: BlockId) -> Result<Bytes> {
        self.calls
            .get(&(to, data.to_vec()))
            .cloned()
            .ok_or_else(|| RpcError::Call(format!("prefetch: no captured read for {to}")))
    }

    async fn code_at(&self, addr: Address, _at: BlockId) -> Result<Bytes> {
        match self.code.get(&addr) {
            Some(Ok(b)) => Ok(b.clone()),
            Some(Err(msg)) => Err(RpcError::Call(msg.clone())),
            // Not pre-fetched → treat as no code (fail-closed: the gate rejects it).
            None => Ok(Bytes::new()),
        }
    }

    async fn logs(&self, _filter: &Filter) -> Result<Vec<Log>> {
        Err(RpcError::Call(UNSUPPORTED.into()))
    }

    async fn subscribe_heads(&self) -> Result<HeadStream> {
        Err(RpcError::Transport(UNSUPPORTED.into()))
    }

    async fn subscribe_logs(&self, _filter: Filter) -> Result<LogStream> {
        Err(RpcError::Transport(UNSUPPORTED.into()))
    }
}
