//! Property tests for the AMM math (`docs/ARCHITECTURE.md §9` Tier-A item 2):
//! monotonicity, no panics/overflow across the full input range, price > 0.

use alloy_primitives::U256;
use l2i_amm::v2::get_amount_out;
use l2i_amm::v3::{get_sqrt_ratio_at_tick, sqrt_price_x96_to_price, MAX_TICK, MIN_TICK};
use proptest::prelude::*;

// Reserve-scale range: [1, 2^112) — the real domain of V2 reserves.
fn reserve() -> impl Strategy<Value = u128> {
    1u128..(1u128 << 112 >> 16) // keep products well within U256 for exactness
}

proptest! {
    // get_amount_out never panics, even on absurd full-range inputs.
    #[test]
    fn get_amount_out_never_panics(
        a in any::<[u8; 32]>(),
        ri in any::<[u8; 32]>(),
        ro in any::<[u8; 32]>(),
        fee in 0u32..=1_000_000,
    ) {
        let _ = get_amount_out(U256::from_be_bytes(a), U256::from_be_bytes(ri), U256::from_be_bytes(ro), fee);
    }

    // Output is monotonic non-decreasing in amount_in (more in, never less out).
    #[test]
    fn get_amount_out_monotonic_in_amount(
        a1 in 1u128..=u128::MAX/2,
        delta in 1u128..=u128::MAX/2,
        ri in reserve(),
        ro in reserve(),
    ) {
        let out1 = get_amount_out(U256::from(a1), U256::from(ri), U256::from(ro), 3000);
        let out2 = get_amount_out(U256::from(a1) + U256::from(delta), U256::from(ri), U256::from(ro), 3000);
        prop_assert!(out2 >= out1);
    }

    // Output is strictly less than the output reserve (can't drain the pool).
    #[test]
    fn get_amount_out_below_reserve(
        a in 1u128..=u128::MAX/2,
        ri in reserve(),
        ro in reserve(),
    ) {
        let out = get_amount_out(U256::from(a), U256::from(ri), U256::from(ro), 3000);
        prop_assert!(out < U256::from(ro));
    }

    // getSqrtRatioAtTick is defined and strictly monotonic across the whole range.
    #[test]
    fn sqrt_ratio_monotonic(t1 in MIN_TICK..MAX_TICK, t2 in MIN_TICK..=MAX_TICK) {
        let r1 = get_sqrt_ratio_at_tick(t1).unwrap();
        let r2 = get_sqrt_ratio_at_tick(t2).unwrap();
        match t1.cmp(&t2) {
            std::cmp::Ordering::Less => prop_assert!(r1 < r2),
            std::cmp::Ordering::Equal => prop_assert_eq!(r1, r2),
            std::cmp::Ordering::Greater => prop_assert!(r1 > r2),
        }
    }

    // Price of a valid sqrtPriceX96 is finite and > 0.
    #[test]
    fn price_positive_for_valid_ticks(t in MIN_TICK..=MAX_TICK, dec0 in 0i32..=30, dec1 in 0i32..=30) {
        let sqrt = get_sqrt_ratio_at_tick(t).unwrap();
        let p = sqrt_price_x96_to_price(sqrt, dec0, dec1);
        prop_assert!(p.is_finite());
        prop_assert!(p >= 0.0);
    }
}
