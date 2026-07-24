//! The [`ChainProvider`] abstraction and its `alloy`-backed implementation.
//!
//! The trait is what the rest of the system programs against, so ingestors and the
//! validation gate are testable against a mock and never bound to a live node.
//! [`AlloyProvider`] is the production impl: a WS provider for subscriptions
//! (`newHeads`, `logs`) plus an HTTP provider for archive reads (seeding,
//! reconciliation) — exactly the split `docs/ARCHITECTURE.md §5` prescribes.

use crate::error::{Result, RpcError};
use crate::frame::HeadSummary;
use crate::multicall::{decode_aggregate3, encode_aggregate3, Call3, Result3};
use alloy::providers::{Provider, ProviderBuilder, RootProvider};
use alloy::rpc::types::eth::{BlockId, TransactionRequest};
use alloy::rpc::types::{Filter, Log};
use alloy_primitives::{Address, Bytes};
use async_trait::async_trait;
use futures::{Stream, StreamExt};
use std::pin::Pin;

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
    /// Archive/reconcile path (always present).
    http: RootProvider,
    /// Subscription path (present when a WS endpoint is configured).
    ws: Option<RootProvider>,
}

impl AlloyProvider {
    /// Connect to a chain's HTTP endpoint, and optionally its WS endpoint.
    ///
    /// The HTTP endpoint seeds and reconciles; the WS endpoint (if given) carries
    /// the live `newHeads`/`logs` hot path. Without WS, subscriptions error and the
    /// ingestor falls back to HTTP polling (a logged, degraded mode).
    pub async fn connect(chain_id: u64, http_url: &str, ws_url: Option<&str>) -> Result<Self> {
        let http = Self::build(http_url).await?;
        let ws = match ws_url {
            Some(u) => Some(Self::build(u).await?),
            None => None,
        };
        Ok(Self { chain_id, http, ws })
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
        self.http
            .get_block_number()
            .await
            .map_err(|e| RpcError::Call(format!("block_number: {e}")))
    }

    async fn gas_price(&self) -> Result<u64> {
        let wei = self
            .http
            .get_gas_price()
            .await
            .map_err(|e| RpcError::Call(format!("gas_price: {e}")))?;
        Ok(u64::try_from(wei).unwrap_or(u64::MAX))
    }

    async fn head(&self, at: BlockId) -> Result<HeadSummary> {
        let block = self
            .http
            .get_block(at)
            .await
            .map_err(|e| RpcError::Call(format!("get_block: {e}")))?
            .ok_or_else(|| RpcError::Call(format!("block {at:?} not found")))?;
        Ok(head_from_block(&block))
    }

    async fn call(&self, to: Address, data: Bytes, at: BlockId) -> Result<Bytes> {
        let tx = TransactionRequest::default().to(to).input(data.into());
        self.http
            .call(tx)
            .block(at)
            .await
            .map_err(|e| RpcError::Call(format!("eth_call {to}: {e}")))
    }

    async fn code_at(&self, addr: Address, at: BlockId) -> Result<Bytes> {
        self.http
            .get_code_at(addr)
            .block_id(at)
            .await
            .map_err(|e| RpcError::Call(format!("get_code {addr}: {e}")))
    }

    async fn logs(&self, filter: &Filter) -> Result<Vec<Log>> {
        self.http
            .get_logs(filter)
            .await
            .map_err(|e| RpcError::Call(format!("get_logs: {e}")))
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
