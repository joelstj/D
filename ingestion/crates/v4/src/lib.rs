//! # l2i-v4 — Uniswap V4 adapter (Unichain-critical)
//!
//! Maps V4 singleton pools onto the engine's `v3` shape. The concentrated-liquidity
//! math is identical to V3; only *how we read* and *how we identify* differ:
//! - [`event`] — decode `PoolManager` `Swap`/`ModifyLiquidity` (keyed by `poolId`),
//!   including the effective `fee` (dynamic-fee pools).
//! - [`stateview`] — `StateView.getSlot0`/`getLiquidity` seed + reconcile.
//! - [`adapter`] — hook-safety gate and applying events to the shared mirror,
//!   emitting `kind:"v3"` with the `poolId` identity.

pub mod adapter;
pub mod error;
pub mod event;
pub mod stateview;

pub use adapter::{apply_v4_modify_liquidity, apply_v4_swap, hook_is_safe};
pub use error::{Result, V4Error};
pub use event::{
    decode_v4_modify_liquidity, decode_v4_swap, decode_v4_swap_parts, V4LiquidityChange,
    V4SwapState,
};
pub use stateview::{effective_fee, reconcile_v4_batch, seed_v4_pools};
