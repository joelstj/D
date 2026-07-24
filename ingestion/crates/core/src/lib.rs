//! # l2i-core
//!
//! The domain types that encode the `l2arb` engine's JSON contract
//! ([`docs/reference/INTEGRATION.md`]) **field-for-field**, plus the decimal-string
//! big-int serde that contract requires.
//!
//! This crate is pure data + (de)serialization: no I/O, no chain access. Every
//! other crate in the workspace builds on it, and its golden tests are the
//! guarantee that what we send the engine is exactly what the engine documented.
//!
//! ## Layout
//! - [`dec`] — [`DecU256`]/[`DecI256`], the decimal-string big-int wrappers.
//! - [`token`] — [`Token`].
//! - [`pool`] — [`Pool`], [`PoolKind`], [`PoolAddress`], [`Blockstamp`], states.
//! - [`request`] — [`DetectRequest`], [`ChainContext`].
//! - [`cross_chain`] — [`CrossChain`], [`Asset`], [`Bridge`], [`Representation`].
//! - [`response`] — [`DetectResponse`], [`Opportunity`], [`Leg`], [`Risk`], etc.
//!
//! [`docs/reference/INTEGRATION.md`]: https://github.com/joelstj/l2_bots/blob/main/docs/reference/INTEGRATION.md

pub mod cross_chain;
pub mod dec;
pub mod pool;
pub mod request;
pub mod response;
pub mod token;

pub use cross_chain::{Asset, Bridge, CrossChain, Representation};
pub use dec::{DecI256, DecU256};
pub use pool::{Blockstamp, Pool, PoolAddress, PoolKind, V2State, V3State};
pub use request::{ChainContext, DetectRequest};
pub use response::{Block, DetectResponse, EngineError, Leg, Opportunity, Risk};
pub use token::Token;

/// The output-envelope schema version this build speaks (`docs/ARCHITECTURE.md
/// §10`). Bumped only on a breaking change to the wire contract.
pub const SCHEMA_VERSION: u32 = 1;
