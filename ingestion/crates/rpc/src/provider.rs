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

/// Map an `alloy` transport error into our [`RpcError`], preserving the
/// distinction [`crate::failover::is_failover_error`] depends on to actually
/// trigger a hand-off to a backup endpoint.
///
/// `alloy_json_rpc::RpcError::is_transport_error()` is `true` exactly when the
/// error occurred **in the transport machinery itself** — connection refused,
/// TLS/DNS failure, a request timeout, a non-2xx HTTP status, a dropped batch
/// response — i.e. nothing that ever became a well-formed JSON-RPC envelope.
/// That is precisely "endpoint-health error", so it maps to
/// [`RpcError::Transport`]. Everything else (`ErrorResp` — a revert or any
/// other application-level JSON-RPC error returned inside a normal 200
/// response, `DeserError`, `NullResp`, …) is a genuine **call result**: valid
/// JSON-RPC servers return errors like reverts as a 200 + `{"error": …}`
/// envelope, not a transport failure, and per JSON-RPC semantics every
/// endpoint evaluating the same call against the same chain state would
/// return it identically — so it stays [`RpcError::Call`] and propagates
/// without a hand-off, exactly as before.
///
/// Before this, every read method below collapsed straight to
/// `RpcError::Call` regardless of `e`'s real shape, so `is_failover_error`
/// (which treats `RpcError::Call` as failover-worthy only via a rate-limit
/// *message* substring) never recognised a raw connection failure — a
/// genuinely dead/unreachable endpoint was retried against itself instead of
/// handed off to a configured backup, defeating failover's primary
/// documented use case.
fn classify(e: alloy::transports::TransportError, what: &str) -> RpcError {
    if e.is_transport_error() {
        RpcError::Transport(format!("{what}: {e}"))
    } else {
        RpcError::Call(format!("{what}: {e}"))
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
                .map_err(|e| classify(e, "block_number"))
        })
        .await
    }

    async fn gas_price(&self) -> Result<u64> {
        self.with_http_failover(|http| async move {
            let wei = http
                .get_gas_price()
                .await
                .map_err(|e| classify(e, "gas_price"))?;
            Ok(u64::try_from(wei).unwrap_or(u64::MAX))
        })
        .await
    }

    async fn head(&self, at: BlockId) -> Result<HeadSummary> {
        self.with_http_failover(|http| async move {
            let block = http
                .get_block(at)
                .await
                .map_err(|e| classify(e, "get_block"))?
                // Not a transport problem — the call succeeded and genuinely
                // found no such block; every endpoint at the same height
                // would agree, so this stays `Call` (no failover hand-off).
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
                    .map_err(|e| classify(e, &format!("eth_call {to}")))
            }
        })
        .await
    }

    async fn code_at(&self, addr: Address, at: BlockId) -> Result<Bytes> {
        self.with_http_failover(|http| async move {
            http.get_code_at(addr)
                .block_id(at)
                .await
                .map_err(|e| classify(e, &format!("get_code {addr}")))
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
                        .map_err(|e| classify(e, &format!("batch add get_code {a}")))?;
                    waiters.push(w);
                }
                batch
                    .send()
                    .await
                    .map_err(|e| classify(e, "get_code batch send"))?;
                let mut out = Vec::with_capacity(addrs.len());
                for (a, w) in addrs.iter().zip(waiters) {
                    out.push(w.await.map_err(|e| classify(e, &format!("get_code {a}")))?);
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
                    .map_err(|e| classify(e, "get_logs"))
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

#[cfg(test)]
mod classify_tests {
    use super::*;
    use alloy::rpc::json_rpc::ErrorPayload;
    use alloy::transports::{RpcError as AlloyRpcError, TransportErrorKind};

    // Regression coverage for the HIGH finding: every read method used to
    // collapse straight to `RpcError::Call`, so `is_failover_error` never saw
    // a connection failure as anything but an ordinary call error and a
    // genuinely dead endpoint was never handed off to a configured backup.

    #[test]
    fn a_raw_transport_failure_classifies_as_failover_worthy() {
        // Mirrors what a real connection-refused/DNS/TLS failure looks like
        // once `reqwest`'s error is wrapped by `TransportErrorKind::custom`
        // (see `alloy-transport-http::reqwest_transport::do_reqwest`).
        let e = TransportErrorKind::custom_str("error trying to connect: tcp connect error");
        let mapped = classify(e, "block_number");
        assert!(matches!(mapped, RpcError::Transport(_)), "got {mapped:?}");
        assert!(
            crate::failover::is_failover_error(&mapped),
            "must be recognised as failover-worthy"
        );
    }

    #[test]
    fn a_non_2xx_http_status_classifies_as_failover_worthy() {
        // A 429/503 (or any non-2xx) from a gateway/load-balancer in front of
        // the node — infra-layer, not an application result.
        let e = TransportErrorKind::http_error(503, "backend unavailable".into());
        let mapped = classify(e, "get_logs");
        assert!(matches!(mapped, RpcError::Transport(_)), "got {mapped:?}");
        assert!(crate::failover::is_failover_error(&mapped));
    }

    #[test]
    fn a_genuine_json_rpc_error_response_stays_a_call_error() {
        // A well-formed JSON-RPC error envelope (what a revert / invalid
        // params / any real application-level error looks like over the
        // wire) must NOT be reclassified as failover-worthy — every endpoint
        // evaluating the same call against the same state returns it
        // identically, so failing over would just burn the backup's budget
        // for the same answer.
        let payload: ErrorPayload =
            serde_json::from_str(r#"{"code":3,"message":"execution reverted"}"#).unwrap();
        let e: AlloyRpcError<TransportErrorKind> = AlloyRpcError::ErrorResp(payload);
        let mapped = classify(e, "eth_call 0xabc");
        assert!(matches!(mapped, RpcError::Call(_)), "got {mapped:?}");
        assert!(!crate::failover::is_failover_error(&mapped));
    }

    #[tokio::test]
    async fn a_genuinely_dead_endpoint_read_fails_over_instead_of_being_swallowed_as_call() {
        // End-to-end through the real production code path (not just
        // `classify` in isolation): connecting the HTTP transport itself
        // never touches the network (alloy builds the client lazily), so
        // this succeeds; the first real read then hits a closed local port —
        // deterministic, no live network dependency, the same failure shape
        // a genuinely unreachable configured RPC endpoint produces.
        let provider = AlloyProvider::connect(1, "http://127.0.0.1:1", None)
            .await
            .expect("building the lazy HTTP client never touches the network");
        let err = provider
            .block_number()
            .await
            .expect_err("port 1 has nothing listening");
        assert!(
            matches!(err, RpcError::Transport(_)),
            "a dead endpoint must classify as a transport error so failover can trigger, got {err:?}"
        );
        assert!(crate::failover::is_failover_error(&err));
    }
}
