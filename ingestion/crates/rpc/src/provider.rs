//! The [`ChainProvider`] abstraction and its `alloy`-backed implementation.
//!
//! The trait is what the rest of the system programs against, so ingestors and the
//! validation gate are testable against a mock and never bound to a live node.
//! [`AlloyProvider`] is the production impl: a WS provider for subscriptions
//! (`newHeads`, `logs`) plus an HTTP provider for archive reads (seeding,
//! reconciliation) — exactly the split `docs/ARCHITECTURE.md §5` prescribes.

use crate::error::{Result, RpcError};
use crate::failover::{run_with_failover, split_endpoints};
use crate::frame::HeadSummary;
use crate::multicall::{decode_aggregate3, encode_aggregate3, Call3, Result3};
use alloy::providers::{Provider, ProviderBuilder, RootProvider};
use alloy::rpc::client::BatchRequest;
use alloy::rpc::types::eth::{BlockId, TransactionRequest};
use alloy::rpc::types::{Filter, Log};
use alloy_primitives::{Address, Bytes};
use async_trait::async_trait;
use futures::{Stream, StreamExt};
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::AtomicUsize;
use std::sync::Arc;

/// A stream of confirmed heads from a `newHeads` subscription.
pub type HeadStream = Pin<Box<dyn Stream<Item = HeadSummary> + Send>>;
/// A stream of logs from a `logs` subscription.
pub type LogStream = Pin<Box<dyn Stream<Item = Log> + Send>>;

/// Read-only access to one chain. Everything the ingestion layer needs from a
/// node lives here; `multicall` is provided in terms of `call`.
#[async_trait]
pub trait ChainProvider: Send + Sync {
    /// The chain id this provider talks to.
    fn chain_id(&self) -> u64;

    /// Latest block height.
    async fn block_number(&self) -> Result<u64>;

    /// `eth_gasPrice` — the L2 execution gas price in wei (saturating to `u64`).
    async fn gas_price(&self) -> Result<u64>;

    /// The header summary at `at` (for seeding and reorg detection).
    async fn head(&self, at: BlockId) -> Result<HeadSummary>;

    /// `eth_call` to `to` with `data` at block `at`.
    async fn call(&self, to: Address, data: Bytes, at: BlockId) -> Result<Bytes>;

    /// `eth_getCode(addr)` at block `at`.
    async fn code_at(&self, addr: Address, at: BlockId) -> Result<Bytes>;

    /// `eth_getCode` for many addresses at block `at`, results in call order.
    ///
    /// The default runs them sequentially (one round-trip each); a transport that
    /// supports JSON-RPC request batching overrides this to a **single** round-trip
    /// (see [`AlloyProvider::code_at_batch`]). The validation gate uses it to prove
    /// code-existence for every configured pool without one request per pool.
    async fn code_at_batch(&self, addrs: &[Address], at: BlockId) -> Result<Vec<Bytes>> {
        let mut out = Vec::with_capacity(addrs.len());
        for a in addrs {
            out.push(self.code_at(*a, at).await?);
        }
        Ok(out)
    }

    /// `eth_getLogs(filter)`.
    async fn logs(&self, filter: &Filter) -> Result<Vec<Log>>;

    /// Batch `calls` through Multicall3 at block `at`, returning raw sub-results.
    /// Provided in terms of [`ChainProvider::call`] so every impl gets it free.
    async fn multicall(&self, calls: Vec<Call3>, at: BlockId) -> Result<Vec<Result3>> {
        let data = encode_aggregate3(calls);
        let ret = self.call(l2i_chains::MULTICALL3, data, at).await?;
        decode_aggregate3(&ret)
    }

    /// Subscribe to `newHeads` (requires a pubsub/WS transport).
    async fn subscribe_heads(&self) -> Result<HeadStream>;

    /// Subscribe to `logs` matching `filter` (requires a pubsub/WS transport).
    async fn subscribe_logs(&self, filter: Filter) -> Result<LogStream>;
}

/// Production [`ChainProvider`]: WS for subscriptions, HTTP for archive reads.
#[derive(Clone)]
pub struct AlloyProvider {
    chain_id: u64,
    /// Archive/reconcile HTTP endpoints: `[0]` is the primary, the rest are backups
    /// failed over to on rate-limit/transport errors. Always at least one.
    http: Vec<RootProvider>,
    /// Index of the currently-active HTTP endpoint (shared across clones), advanced
    /// by [`run_with_failover`] and sticky to whichever endpoint answers.
    http_active: Arc<AtomicUsize>,
    /// Subscription path (present when a WS endpoint is configured).
    ws: Option<RootProvider>,
}

impl AlloyProvider {
    /// Connect to a chain's HTTP endpoint(s), and optionally its WS endpoint(s).
    ///
    /// Both `http_url` and `ws_url` may be **comma-separated** lists — a primary
    /// followed by backups. The HTTP list seeds and reconciles: reads run against the
    /// active endpoint and transparently fail over to the next on a rate-limit or
    /// transport error (see [`crate::failover`]), so a provider hitting its rate limit
    /// hands off to the backup with no lost reads. The WS list carries the live
    /// `newHeads`/`logs` hot path; the first endpoint that connects is used (the
    /// supervisor re-runs `connect` on a drop, re-trying the list). Without WS,
    /// subscriptions error and the ingestor falls back to HTTP polling (a logged,
    /// degraded mode).
    pub async fn connect(chain_id: u64, http_url: &str, ws_url: Option<&str>) -> Result<Self> {
        let http_urls = split_endpoints(http_url);
        if http_urls.is_empty() {
            return Err(RpcError::Transport(
                "no HTTP endpoint configured (http_url is empty)".into(),
            ));
        }
        let mut http = Vec::with_capacity(http_urls.len());
        for u in &http_urls {
            http.push(Self::build(u).await?);
        }
        if http.len() > 1 {
            tracing::info!(
                chain_id,
                http_endpoints = http.len(),
                "HTTP endpoint failover enabled (1 primary + backups)"
            );
        }

        // WS: try each configured endpoint in order; use the first that connects.
        let ws = match ws_url {
            Some(list) => {
                let mut chosen = None;
                for u in split_endpoints(list) {
                    match Self::build(&u).await {
                        Ok(p) => {
                            chosen = Some(p);
                            break;
                        }
                        Err(e) => {
                            tracing::warn!(chain_id, endpoint = %u, error = %e, "WS endpoint unavailable; trying next")
                        }
                    }
                }
                chosen
            }
            None => None,
        };

        Ok(Self {
            chain_id,
            http,
            http_active: Arc::new(AtomicUsize::new(0)),
            ws,
        })
    }

    /// Build a read-only [`RootProvider`], picking the transport by URL scheme
    /// (`http(s)` → HTTP, `ws(s)` → WebSocket).
    async fn build(url: &str) -> Result<RootProvider> {
        if url.starts_with("http://") || url.starts_with("https://") {
            let parsed = url::Url::parse(url)
                .map_err(|e| RpcError::Transport(format!("bad url {url}: {e}")))?;
            let provider = ProviderBuilder::new()
                .disable_recommended_fillers()
                .connect_http(parsed);
            Ok(provider.root().clone())
        } else {
            // ws / wss / ipc — the async connection-string path handles these.
            let provider = ProviderBuilder::new()
                .disable_recommended_fillers()
                .connect(url)
                .await
                .map_err(|e| RpcError::Transport(format!("connect {url}: {e}")))?;
            Ok(provider.root().clone())
        }
    }

    /// Run one HTTP read through the failover state machine: `op` is invoked with the
    /// active endpoint's provider and re-invoked against the next endpoint on a
    /// rate-limit/transport error, sticking to whichever answers. A genuine call error
    /// (e.g. a revert) propagates without a hand-off.
    async fn with_http_failover<T, F, Fut>(&self, op: F) -> Result<T>
    where
        F: Fn(RootProvider) -> Fut,
        Fut: Future<Output = Result<T>>,
    {
        run_with_failover(self.http.len(), &self.http_active, |i| {
            op(self.http[i].clone())
        })
        .await
    }

    fn subscriber(&self) -> Result<&RootProvider> {
        self.ws.as_ref().ok_or_else(|| {
            RpcError::Transport("no WS endpoint configured for subscriptions".into())
        })
    }
}

fn head_from_block(b: &alloy::rpc::types::Block) -> HeadSummary {
    HeadSummary {
        number: b.header.number,
        hash: b.header.hash,
        parent_hash: b.header.inner.parent_hash,
        timestamp: b.header.inner.timestamp,
    }
}

#[async_trait]
impl ChainProvider for AlloyProvider {
    fn chain_id(&self) -> u64 {
        self.chain_id
    }

    async fn block_number(&self) -> Result<u64> {
        self.with_http_failover(|http| async move {
            http.get_block_number()
                .await
                .map_err(|e| RpcError::Call(format!("block_number: {e}")))
        })
        .await
    }

    async fn gas_price(&self) -> Result<u64> {
        self.with_http_failover(|http| async move {
            let wei = http
                .get_gas_price()
                .await
                .map_err(|e| RpcError::Call(format!("gas_price: {e}")))?;
            Ok(u64::try_from(wei).unwrap_or(u64::MAX))
        })
        .await
    }

    async fn head(&self, at: BlockId) -> Result<HeadSummary> {
        self.with_http_failover(|http| async move {
            let block = http
                .get_block(at)
                .await
                .map_err(|e| RpcError::Call(format!("get_block: {e}")))?
                .ok_or_else(|| RpcError::Call(format!("block {at:?} not found")))?;
            Ok(head_from_block(&block))
        })
        .await
    }

    async fn call(&self, to: Address, data: Bytes, at: BlockId) -> Result<Bytes> {
        self.with_http_failover(|http| {
            let data = data.clone();
            async move {
                let tx = TransactionRequest::default().to(to).input(data.into());
                http.call(tx)
                    .block(at)
                    .await
                    .map_err(|e| RpcError::Call(format!("eth_call {to}: {e}")))
            }
        })
        .await
    }

    async fn code_at(&self, addr: Address, at: BlockId) -> Result<Bytes> {
        self.with_http_failover(|http| async move {
            http.get_code_at(addr)
                .block_id(at)
                .await
                .map_err(|e| RpcError::Call(format!("get_code {addr}: {e}")))
        })
        .await
    }

    /// One JSON-RPC batch carrying every `eth_getCode` — a single HTTP round-trip
    /// regardless of pool count, so booting a chain with N pools costs one request
    /// here instead of N. Fails over to a backup endpoint as a unit.
    async fn code_at_batch(&self, addrs: &[Address], at: BlockId) -> Result<Vec<Bytes>> {
        if addrs.is_empty() {
            return Ok(vec![]);
        }
        let addrs = addrs.to_vec();
        self.with_http_failover(|http| {
            let addrs = addrs.clone();
            async move {
                let mut batch = BatchRequest::new(http.client());
                let mut waiters = Vec::with_capacity(addrs.len());
                for a in &addrs {
                    let w = batch
                        .add_call::<(Address, BlockId), Bytes>("eth_getCode", &(*a, at))
                        .map_err(|e| RpcError::Call(format!("batch add get_code {a}: {e}")))?;
                    waiters.push(w);
                }
                batch
                    .send()
                    .await
                    .map_err(|e| RpcError::Call(format!("get_code batch send: {e}")))?;
                let mut out = Vec::with_capacity(addrs.len());
                for (a, w) in addrs.iter().zip(waiters) {
                    out.push(
                        w.await
                            .map_err(|e| RpcError::Call(format!("get_code {a}: {e}")))?,
                    );
                }
                Ok(out)
            }
        })
        .await
    }

    async fn logs(&self, filter: &Filter) -> Result<Vec<Log>> {
        self.with_http_failover(|http| {
            let filter = filter.clone();
            async move {
                http.get_logs(&filter)
                    .await
                    .map_err(|e| RpcError::Call(format!("get_logs: {e}")))
            }
        })
        .await
    }

    async fn subscribe_heads(&self) -> Result<HeadStream> {
        let sub = self
            .subscriber()?
            .subscribe_blocks()
            .await
            .map_err(|e| RpcError::Transport(format!("subscribe newHeads: {e}")))?;
        let stream = sub.into_stream().map(|h| HeadSummary {
            number: h.number,
            hash: h.hash,
            parent_hash: h.inner.parent_hash,
            timestamp: h.inner.timestamp,
        });
        Ok(Box::pin(stream))
    }

    async fn subscribe_logs(&self, filter: Filter) -> Result<LogStream> {
        let sub = self
            .subscriber()?
            .subscribe_logs(&filter)
            .await
            .map_err(|e| RpcError::Transport(format!("subscribe logs: {e}")))?;
        Ok(Box::pin(sub.into_stream()))
    }
}
