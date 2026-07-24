//! # l2i-aggregator
//!
//! Assembles synchronized cross-chain snapshots and the `DetectRequest` the engine
//! consumes:
//! - [`snapshot`] — atomic per-chain re-stamping (one block per chain per request)
//!   and incremental-delta tracking (send only changed pools).
//! - [`request`] — the `DetectRequest` builder and the first-request-is-full policy.
//! - [`cadence`] — debounce floor + heartbeat ceiling for *when* to send.
//! - [`crosschain`] — prune `cross_chain` to usable assets/bridges/pairs.

pub mod cadence;
pub mod crosschain;
pub mod request;
pub mod snapshot;

pub use cadence::{Cadence, CadenceMode};
pub use crosschain::filter_cross_chain;
pub use request::{build_detect_request, ChainSnapshot, IncrementalPolicy, RequestConfig};
pub use snapshot::{re_stamp, state_fingerprint, IncrementalTracker};
