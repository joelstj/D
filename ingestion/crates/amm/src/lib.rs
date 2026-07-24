//! # l2i-amm — pure AMM math
//!
//! No I/O, exhaustively unit-tested. Three concerns:
//! - [`v2`] — constant-product (`x·y=k`) `getAmountOut`/`getAmountIn`.
//! - [`v3`] — concentrated-liquidity tick math (`getSqrtRatioAtTick`,
//!   `sqrtPriceX96 → price`), a faithful port of Uniswap v3-core, shared by V4.
//! - [`native`] — `native_price_in[T]` derivation from WETH/T pools.
//!
//! The engine performs the exact slippage math for opportunities; this crate is the
//! pricing our own layer needs (native-price gas-costing) plus the tick math that
//! cross-checks a pool's `(tick, sqrtPriceX96)` against the chain.

pub mod native;
pub mod v2;
pub mod v3;

pub use native::{
    build_native_price_map, native_price_in, v2_price_native_in_t, v3_price_native_in_t,
};
pub use v2::{get_amount_in, get_amount_out};
pub use v3::{get_sqrt_ratio_at_tick, sqrt_price_x96_to_price, MAX_TICK, MIN_TICK};
