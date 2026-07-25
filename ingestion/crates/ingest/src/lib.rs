//! # l2i-ingest — the per-chain ingestor
//!
//! Turns the log the node already pushed into verified, block-stamped pool state:
//! - [`event`] — decode `Sync` (V2) straight from the log (zero extra round-trip).
//! - [`mirror`] — the in-memory [`Mirror`](mirror::Mirror) of every pool's current
//!   state, renderable as the engine's `Pool` object.
//! - [`v2`] — the V2 path: `getReserves` multicall seed + live `Sync` updates.
//!
//! V3 (`Swap`/`Mint`/`Burn`) and V4 arrive in later milestones; they share the
//! mirror and blockstamping built here.

pub mod error;
pub mod event;
pub mod mirror;
pub mod persist;
pub mod reconcile;
pub mod reorg;
pub mod v2;
pub mod v3;

pub use error::{IngestError, Result};
pub use mirror::{LiveState, Mirror, PoolState};
pub use persist::{load_snapshot, write_mirror, write_snapshot, MirrorSnapshot};
pub use reconcile::{
    reconcile_batch, reconcile_pool, reconcile_v2, reconcile_v3, ReconcileResult, ReconcileTally,
};
pub use reorg::{BlockRef, ReorgOutcome, ReorgTracker};
