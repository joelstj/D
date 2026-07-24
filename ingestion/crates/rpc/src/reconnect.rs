//! Supervised reconnect with exponential backoff.
//!
//! The connect loop is factored so the *logic* (attempt counting, backoff between
//! failures, giving up after `max_attempts`) is independent of real time and the
//! real transport: it takes a `connect` closure and a [`Sleeper`]. Production wires
//! in [`TokioSleeper`] and a real WS connector; tests wire in a [`RecordingSleeper`]
//! and a mock connector, so the state machine is proven deterministically with no
//! wall-clock waits.

use crate::backoff::{Backoff, BackoffPolicy};
use crate::error::{Result, RpcError};
use async_trait::async_trait;
use std::future::Future;
use std::time::Duration;

/// Retry configuration for a connect loop.
#[derive(Clone, Copy, Debug, Default)]
pub struct RetryConfig {
    /// Backoff schedule for the waits between failed attempts.
    pub policy: BackoffPolicy,
    /// Give up after this many total attempts (`None` = retry forever).
    pub max_attempts: Option<u32>,
    /// Fixed jitter seed when set (deterministic — tests); `None` = entropy.
    pub jitter_seed: Option<u64>,
}

/// Something that can wait for a duration (abstracted for testability).
#[async_trait]
pub trait Sleeper: Send {
    /// Wait for `d`.
    async fn sleep(&mut self, d: Duration);
}

/// Real time via `tokio::time::sleep`.
#[derive(Clone, Copy, Debug, Default)]
pub struct TokioSleeper;

#[async_trait]
impl Sleeper for TokioSleeper {
    async fn sleep(&mut self, d: Duration) {
        tokio::time::sleep(d).await;
    }
}

/// Records the delays it is asked to sleep, and returns instantly (tests).
#[derive(Clone, Debug, Default)]
pub struct RecordingSleeper {
    /// The sequence of durations passed to [`Sleeper::sleep`].
    pub delays: Vec<Duration>,
}

#[async_trait]
impl Sleeper for RecordingSleeper {
    async fn sleep(&mut self, d: Duration) {
        self.delays.push(d);
    }
}

/// The outcome of a completed connect loop.
#[derive(Debug)]
pub struct Connected<T> {
    /// The established connection.
    pub conn: T,
    /// How many attempts it took (1 = first try succeeded).
    pub attempts: u32,
}

/// Attempt to connect, retrying failed attempts with jittered backoff until
/// success or `max_attempts` is exhausted.
///
/// `connect(attempt)` is called with the 0-based attempt index; between failures
/// we sleep the backoff delay for that attempt.
pub async fn connect_with_retry<T, C, Fut, S>(
    cfg: RetryConfig,
    mut connect: C,
    sleeper: &mut S,
) -> Result<Connected<T>>
where
    C: FnMut(u32) -> Fut,
    Fut: Future<Output = Result<T>>,
    S: Sleeper,
{
    let mut backoff = match cfg.jitter_seed {
        Some(seed) => Backoff::with_seed(cfg.policy, seed),
        None => Backoff::new(cfg.policy),
    };

    let mut attempt: u32 = 0;
    loop {
        match connect(attempt).await {
            Ok(conn) => {
                return Ok(Connected {
                    conn,
                    attempts: attempt + 1,
                })
            }
            Err(e) => {
                attempt += 1;
                if let Some(max) = cfg.max_attempts {
                    if attempt >= max {
                        return Err(RpcError::Transport(format!(
                            "gave up after {attempt} attempts; last error: {e}"
                        )));
                    }
                }
                let delay = backoff.next_delay();
                tracing::debug!(attempt, ?delay, error = %e, "connect failed; backing off");
                sleeper.sleep(delay).await;
            }
        }
    }
}

/// Coarse connection state, surfaced to observability and the `verified` flag: a
/// chain that is reconnecting must emit `verified:false` until it re-seeds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConnState {
    /// Live and healthy.
    Connected,
    /// Down / re-establishing; downstream data is not currently verifiable.
    Reconnecting,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::Arc;

    #[tokio::test]
    async fn succeeds_first_try_no_sleep() {
        let mut sleeper = RecordingSleeper::default();
        let out = connect_with_retry(
            RetryConfig::default(),
            |_attempt| async { Ok::<_, RpcError>("conn") },
            &mut sleeper,
        )
        .await
        .unwrap();
        assert_eq!(out.conn, "conn");
        assert_eq!(out.attempts, 1);
        assert!(sleeper.delays.is_empty(), "no backoff on first-try success");
    }

    #[tokio::test]
    async fn retries_then_succeeds_with_backoff_schedule() {
        // Fail the first 3 attempts, succeed on the 4th.
        let fails = Arc::new(AtomicU32::new(0));
        let mut sleeper = RecordingSleeper::default();
        let cfg = RetryConfig {
            jitter_seed: Some(7),
            ..Default::default()
        };
        let out = connect_with_retry(
            cfg,
            |attempt| {
                let fails = fails.clone();
                async move {
                    if fails.load(Ordering::SeqCst) < 3 {
                        fails.fetch_add(1, Ordering::SeqCst);
                        Err(RpcError::Transport(format!("boom {attempt}")))
                    } else {
                        Ok::<_, RpcError>(attempt)
                    }
                }
            },
            &mut sleeper,
        )
        .await
        .unwrap();
        assert_eq!(out.attempts, 4);
        assert_eq!(out.conn, 3); // succeeded on attempt index 3
                                 // Slept exactly 3 times (once per failure); each delay within its ceiling.
        assert_eq!(sleeper.delays.len(), 3);
        let p = BackoffPolicy::default();
        for (i, d) in sleeper.delays.iter().enumerate() {
            assert!(
                *d <= p.ceiling(i as u32),
                "delay {d:?} > ceiling for attempt {i}"
            );
        }
    }

    #[tokio::test]
    async fn gives_up_after_max_attempts() {
        let mut sleeper = RecordingSleeper::default();
        let cfg = RetryConfig {
            max_attempts: Some(3),
            jitter_seed: Some(1),
            ..Default::default()
        };
        let err = connect_with_retry(
            cfg,
            |attempt| async move { Err::<(), _>(RpcError::Transport(format!("no {attempt}"))) },
            &mut sleeper,
        )
        .await
        .unwrap_err();
        assert!(matches!(err, RpcError::Transport(_)));
        // 3 attempts total → slept twice (after attempts 0 and 1; attempt 2 hits the cap).
        assert_eq!(sleeper.delays.len(), 2);
    }
}
