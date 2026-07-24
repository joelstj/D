//! Uniswap V3 / V4 concentrated-liquidity tick math.
//!
//! [`get_sqrt_ratio_at_tick`] is a faithful port of Uniswap v3-core `TickMath`
//! (fixed-point, exact), so it reproduces on-chain `sqrtPriceX96` bit-for-bit. It
//! is the cross-check that our understanding of a pool's `(tick, sqrtPriceX96)`
//! matches the chain. [`sqrt_price_x96_to_price`] converts a `sqrtPriceX96` to the
//! human price of `token0` in `token1`, which feeds native-price derivation.

use alloy_primitives::U256;

/// Minimum usable tick.
pub const MIN_TICK: i32 = -887272;
/// Maximum usable tick.
pub const MAX_TICK: i32 = 887272;
/// `getSqrtRatioAtTick(MIN_TICK)`.
pub const MIN_SQRT_RATIO: u128 = 4295128739;
/// `getSqrtRatioAtTick(MAX_TICK)` (fits in 160 bits, not 128 — kept as a string).
pub const MAX_SQRT_RATIO_DEC: &str = "1461446703485210103287273052203988822378723970342";

/// `2⁹⁶` — the fixed-point scale of `sqrtPriceX96`.
pub fn q96() -> U256 {
    U256::from(1u8) << 96
}

/// The `sqrtPriceX96` for a tick — a faithful port of Uniswap v3-core
/// `TickMath.getSqrtRatioAtTick`. Returns `None` if `tick` is out of range.
pub fn get_sqrt_ratio_at_tick(tick: i32) -> Option<U256> {
    if !(MIN_TICK..=MAX_TICK).contains(&tick) {
        return None;
    }
    let abs_tick = tick.unsigned_abs();

    // Each set bit of abs_tick multiplies in a precomputed Q128.128 factor.
    let mut ratio = if abs_tick & 0x1 != 0 {
        U256::from_str_radix("fffcb933bd6fad37aa2d162d1a594001", 16).unwrap()
    } else {
        U256::from_str_radix("100000000000000000000000000000000", 16).unwrap()
    };

    const FACTORS: [(u32, &str); 19] = [
        (0x2, "fff97272373d413259a46990580e213a"),
        (0x4, "fff2e50f5f656932ef12357cf3c7fdcc"),
        (0x8, "ffe5caca7e10e4e61c3624eaa0941cd0"),
        (0x10, "ffcb9843d60f6159c9db58835c926644"),
        (0x20, "ff973b41fa98c081472e6896dfb254c0"),
        (0x40, "ff2ea16466c96a3843ec78b326b52861"),
        (0x80, "fe5dee046a99a2a811c461f1969c3053"),
        (0x100, "fcbe86c7900a88aedcffc83b479aa3a4"),
        (0x200, "f987a7253ac413176f2b074cf7815e54"),
        (0x400, "f3392b0822b70005940c7a398e4b70f3"),
        (0x800, "e7159475a2c29b7443b29c7fa6e889d9"),
        (0x1000, "d097f3bdfd2022b8845ad8f792aa5825"),
        (0x2000, "a9f746462d870fdf8a65dc1f90e061e5"),
        (0x4000, "70d869a156d2a1b890bb3df62baf32f7"),
        (0x8000, "31be135f97d08fd981231505542fcfa6"),
        (0x10000, "9aa508b5b7a84e1c677de54f3e99bc9"),
        (0x20000, "5d6af8dedb81196699c329225ee604"),
        (0x40000, "2216e584f5fa1ea926041bedfe98"),
        (0x80000, "48a170391f7dc42444e8fa2"),
    ];
    for (bit, factor) in FACTORS {
        if abs_tick & bit != 0 {
            let f = U256::from_str_radix(factor, 16).unwrap();
            ratio = (ratio.wrapping_mul(f)) >> 128;
        }
    }

    if tick > 0 {
        ratio = U256::MAX / ratio;
    }

    // Downcast Q128.128 → Q96 (>> 32), rounding up.
    let shifted = ratio >> 32;
    let round_up = if (ratio & U256::from(u32::MAX)).is_zero() {
        U256::ZERO
    } else {
        U256::from(1u8)
    };
    Some(shifted + round_up)
}

/// The human price of `token0` in `token1` implied by `sqrt_price_x96`:
/// `(sqrtP/2⁹⁶)² · 10^(dec0−dec1)`.
///
/// Uses `f64` — appropriate for the gas-costing `native_price_in` ratio (the engine
/// does exact slippage math itself). Returns `0.0` for a zero price.
pub fn sqrt_price_x96_to_price(sqrt_price_x96: U256, dec0: i32, dec1: i32) -> f64 {
    // ratio = (sqrtP / 2^96)^2, computed in f64 via a two-step scale to keep
    // magnitude in range.
    let sqrt = u256_to_f64(sqrt_price_x96);
    let q = 2f64.powi(96);
    let raw = (sqrt / q) * (sqrt / q); // (sqrtP/2^96)^2 = token1_raw per token0_raw
    raw * 10f64.powi(dec0 - dec1)
}

/// Best-effort `U256 → f64` (lossy for very large values, fine for ratios).
pub fn u256_to_f64(v: U256) -> f64 {
    // Sum the 64-bit limbs weighted by 2^(64k). `as_limbs` is little-endian.
    let limbs = v.as_limbs();
    let mut acc = 0f64;
    for (i, &limb) in limbs.iter().enumerate() {
        acc += (limb as f64) * 2f64.powi(64 * i as i32);
    }
    acc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tick_zero_is_q96() {
        assert_eq!(get_sqrt_ratio_at_tick(0).unwrap(), q96());
        assert_eq!(
            get_sqrt_ratio_at_tick(0).unwrap(),
            U256::from_str_radix("79228162514264337593543950336", 10).unwrap()
        );
    }

    #[test]
    fn min_max_tick_known_answers() {
        assert_eq!(
            get_sqrt_ratio_at_tick(MIN_TICK).unwrap(),
            U256::from(MIN_SQRT_RATIO)
        );
        assert_eq!(
            get_sqrt_ratio_at_tick(MAX_TICK).unwrap(),
            U256::from_str_radix(MAX_SQRT_RATIO_DEC, 10).unwrap()
        );
    }

    #[test]
    fn out_of_range_ticks_rejected() {
        assert!(get_sqrt_ratio_at_tick(MIN_TICK - 1).is_none());
        assert!(get_sqrt_ratio_at_tick(MAX_TICK + 1).is_none());
    }

    #[test]
    fn monotonic_around_zero() {
        let mut prev = U256::ZERO;
        for t in [-100, -10, -1, 0, 1, 10, 100] {
            let r = get_sqrt_ratio_at_tick(t).unwrap();
            assert!(r > prev, "sqrtRatio not increasing at tick {t}");
            prev = r;
        }
    }

    #[test]
    fn price_of_symmetric_pool_is_one() {
        // At tick 0 with equal decimals, price is 1.0.
        let p = sqrt_price_x96_to_price(q96(), 18, 18);
        assert!((p - 1.0).abs() < 1e-9, "price {p} != 1.0");
    }
}
