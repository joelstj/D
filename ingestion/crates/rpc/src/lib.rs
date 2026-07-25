//! # l2i-rpc — RPC & transport layer
//!
//! Reliable, low-latency access to the five L2 chains, built on `alloy`:
//!
//! - [`provider`] — the [`ChainProvider`](provider::ChainProvider) trait the whole
//!   system programs against, plus [`AlloyProvider`](provider::AlloyProvider) (WS
//!   subscriptions + HTTP archive reads).
//! - [`multicall`] — Multicall3 `aggregate3` encode/decode (batched seeding /
//!   reconciliation), proven against a recorded on-chain response.
//! - [`prefetch`] — [`PrefetchProvider`](prefetch::PrefetchProvider): batch every
//!   read once (getCode batch + chunked multicall), then replay them offline so
//!   boot-time per-item logic (e.g. the validation gate) costs O(1) requests.
//! - [`frame`] — WebSocket subscription frame decoding (`newHeads` / `logs`).
//! - [`backoff`] — exponential backoff with full jitter.
//! - [`reconnect`] — supervised reconnect loop over a mockable transport + clock.
//! - [`coalesce`] — request coalescing (single-flight) to dedupe concurrent reads.
//!
//! The deterministic pieces (backoff, multicall codec, frame decode, reconnect
//! state machine, coalescing) are unit-tested with recorded real data and mocks
//! (Tier A). Live connectivity is exercised by the Tier-B smoke harness.

pub mod backoff;
pub mod coalesce;
pub mod error;
pub mod frame;
#[cfg(feature = "testing")]
pub mod mock;
pub mod multicall;
pub mod prefetch;
pub mod provider;
pub mod reconnect;

pub use error::{Result, RpcError};
pub use frame::{HeadSummary, SubscriptionNotification};
pub use prefetch::PrefetchProvider;
pub use provider::{AlloyProvider, ChainProvider};

// Re-export the alloy RPC types our trait surface exposes, so downstream crates
// program against `l2i_rpc::{BlockId, Filter, Log}` without each depending on the
// alloy umbrella directly.
pub use alloy::rpc::types::eth::{BlockId, BlockNumberOrTag};
pub use alloy::rpc::types::{Filter, Log};
