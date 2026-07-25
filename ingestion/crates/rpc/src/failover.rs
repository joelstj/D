//! Secondary-endpoint failover for the HTTP read path.
//!
//! A chain's `http_url` may list **several** endpoints (comma-separated in config):
//! a primary and one or more backups. When the active endpoint answers a read with a
//! **rate-limit** or **transport** error, [`run_with_failover`] transparently retries
//! the same read on the next endpoint and *sticks* to whichever one answers, so a
//! provider hitting its rate limit hands off to the backup with no caller changes and
//! no lost reads.
//!
//! Crucially it never fails over on a genuine call *result* — an `eth_call` revert,
//! a decode error, a Multicall sub-call revert — because every endpoint would return
//! that identically; failing over would just burn the backup's budget and return the
//! same answer. Only endpoint-health errors trigger a hand-off. The control flow is
//! transport-agnostic (it takes a per-endpoint async closure), so the state machine
//! is unit-tested deterministically with no network — mirroring [`crate::reconnect`].

use crate::error::{Result, RpcError};
use std::future::Future;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Split a comma-separated endpoint list (`"https://a , https://b"`) into trimmed,
/// non-empty URLs, preserving order (primary first). Empties are dropped, so a
/// single URL, a trailing comma, or stray whitespace all parse sensibly.
pub fn split_endpoints(list: &str) -> Vec<String> {
    list.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect()
}

/// Whether `e` is an endpoint-health error that warrants trying the next endpoint
/// (rate-limit or transport/timeout), as opposed to a genuine call result (a revert,
/// a decode failure) that every endpoint returns identically and must propagate.
pub fn is_failover_error(e: &RpcError) -> bool {
    match e {
        // Connectivity / TLS / socket / deadline — a different endpoint may be up.
        RpcError::Transport(_) | RpcError::Timeout(_) => true,
        // A node-returned error: fail over only when it reads as rate-limiting.
        RpcError::Call(msg) => is_rate_limit_message(msg),
        // Reverts, decode failures, closed subscriptions: not an endpoint problem.
        RpcError::Decode(_) | RpcError::SubscriptionClosed | RpcError::MulticallReverted { .. } => {
            false
        }
    }
}

/// Recognise the rate-limit / throttling signals providers return (HTTP 429, the
/// common JSON-RPC `-32005` "limit exceeded", Alchemy/Infura/QuickNode wordings).
fn is_rate_limit_message(msg: &str) -> bool {
    let m = msg.to_ascii_lowercase();
    const MARKERS: &[&str] = &[
        "429",
        "rate limit",
        "rate-limit",
        "ratelimit",
        "too many requests",
        "too many concurrent",
        "limit exceeded",
        "-32005",
        "-32016",
        "-32029",
        "capacity",
        "throttl",
        "compute unit",
        "over quota",
        "request limit",
    ];
    MARKERS.iter().any(|k| m.contains(k))
}

/// Run `op` against endpoints, starting from `*active`, failing over to the next on a
/// rate-limit/transport error and **sticking** to whichever endpoint succeeds (so the
/// next call starts there). Tries each of the `n` endpoints at most once; returns the
/// first success, or the last failover-triggering error once all are exhausted. A
/// non-failover error (e.g. a revert) is returned immediately, without a hand-off.
pub async fn run_with_failover<T, F, Fut>(n: usize, active: &AtomicUsize, op: F) -> Result<T>
where
    F: Fn(usize) -> Fut,
    Fut: Future<Output = Result<T>>,
{
    if n == 0 {
        return Err(RpcError::Transport("no RPC endpoints configured".into()));
    }
    let start = active.load(Ordering::Relaxed) % n;
    let mut last_err: Option<RpcError> = None;
    for k in 0..n {
        let idx = (start + k) % n;
        match op(idx).await {
            Ok(v) => {
                active.store(idx, Ordering::Relaxed); // stick to the working endpoint
                return Ok(v);
            }
            Err(e) if is_failover_error(&e) => {
                tracing::warn!(
                    endpoint = idx,
                    error = %e,
                    "RPC endpoint failed over (rate-limit/transport); trying next"
                );
                last_err = Some(e);
            }
            // A real result error — do not fail over; every endpoint would agree.
            Err(e) => return Err(e),
        }
    }
    Err(last_err.unwrap_or_else(|| RpcError::Transport("all RPC endpoints unavailable".into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    fn rate_limited() -> RpcError {
        RpcError::Call("server error: 429 Too Many Requests".into())
    }

    #[test]
    fn split_endpoints_trims_and_drops_empties() {
        assert_eq!(
            split_endpoints("https://a , https://b ,, "),
            vec!["https://a".to_string(), "https://b".to_string()]
        );
        assert_eq!(
            split_endpoints("https://only"),
            vec!["https://only".to_string()]
        );
        assert!(split_endpoints("  ,  ,").is_empty());
    }

    #[test]
    fn classifies_rate_limit_and_transport_but_not_reverts() {
        assert!(is_failover_error(&rate_limited()));
        assert!(is_failover_error(&RpcError::Call(
            "{\"code\":-32005,\"message\":\"limit exceeded\"}".into()
        )));
        assert!(is_failover_error(&RpcError::Transport(
            "connection refused".into()
        )));
        assert!(is_failover_error(&RpcError::Timeout(
            std::time::Duration::from_secs(1)
        )));
        // Genuine results every endpoint would return identically — never fail over.
        assert!(!is_failover_error(&RpcError::Call(
            "execution reverted".into()
        )));
        assert!(!is_failover_error(&RpcError::Decode("bad abi".into())));
        assert!(!is_failover_error(&RpcError::MulticallReverted {
            index: 2
        }));
    }

    #[tokio::test]
    async fn fails_over_to_secondary_and_sticks() {
        let active = AtomicUsize::new(0);
        let attempts = AtomicUsize::new(0);
        // Primary (0) always rate-limits; secondary (1) is healthy.
        let op = |i: usize| {
            attempts.fetch_add(1, Ordering::SeqCst);
            async move {
                if i == 0 {
                    Err(rate_limited())
                } else {
                    Ok::<u32, RpcError>(42)
                }
            }
        };
        let got = run_with_failover(2, &active, &op).await.unwrap();
        assert_eq!(got, 42);
        assert_eq!(attempts.load(Ordering::SeqCst), 2, "primary then secondary");
        assert_eq!(
            active.load(Ordering::SeqCst),
            1,
            "stuck to the working secondary"
        );

        // A subsequent call starts at the secondary and needs just one attempt.
        attempts.store(0, Ordering::SeqCst);
        let got2 = run_with_failover(2, &active, &op).await.unwrap();
        assert_eq!(got2, 42);
        assert_eq!(
            attempts.load(Ordering::SeqCst),
            1,
            "no wasted retry on the good endpoint"
        );
    }

    #[tokio::test]
    async fn revert_propagates_without_failover() {
        let active = AtomicUsize::new(0);
        let attempts = AtomicUsize::new(0);
        let op = |_i: usize| {
            attempts.fetch_add(1, Ordering::SeqCst);
            async move { Err::<u32, RpcError>(RpcError::Call("execution reverted".into())) }
        };
        let err = run_with_failover(2, &active, &op).await.unwrap_err();
        assert!(matches!(err, RpcError::Call(_)));
        assert_eq!(
            attempts.load(Ordering::SeqCst),
            1,
            "a revert is not retried elsewhere"
        );
        assert_eq!(
            active.load(Ordering::SeqCst),
            0,
            "active endpoint unchanged"
        );
    }

    #[tokio::test]
    async fn all_endpoints_rate_limited_returns_last_error() {
        let active = AtomicUsize::new(0);
        let attempts = AtomicUsize::new(0);
        let op = |_i: usize| {
            attempts.fetch_add(1, Ordering::SeqCst);
            async move { Err::<u32, RpcError>(rate_limited()) }
        };
        let err = run_with_failover(3, &active, &op).await.unwrap_err();
        assert!(is_failover_error(&err));
        assert_eq!(
            attempts.load(Ordering::SeqCst),
            3,
            "tried every endpoint once"
        );
    }
}
