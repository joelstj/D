//! Uniswap V2 constant-product (`x·y=k`) math.

use alloy_primitives::U256;

/// Fee denominator: `fee_pips` is in millionths, so the fee-adjusted input
/// multiplier is `(1_000_000 - fee_pips)` over `1_000_000`.
const PIPS_DENOM: u32 = 1_000_000;

/// Exact Uniswap-V2 `getAmountOut`: the output of swapping `amount_in` of the
/// input token into a pool with `(reserve_in, reserve_out)` at `fee_pips`.
///
/// `out = (amount_in·(1e6−fee)·reserve_out) / (reserve_in·1e6 + amount_in·(1e6−fee))`.
///
/// Saturating arithmetic — never panics, even on absurd (non-reserve-scale) inputs
/// well past 2¹¹². For real reserve-range inputs no saturation occurs and the
/// result is exact.
pub fn get_amount_out(amount_in: U256, reserve_in: U256, reserve_out: U256, fee_pips: u32) -> U256 {
    if amount_in.is_zero() || reserve_in.is_zero() || reserve_out.is_zero() {
        return U256::ZERO;
    }
    let fee_factor = U256::from(PIPS_DENOM.saturating_sub(fee_pips.min(PIPS_DENOM)));
    let amount_in_with_fee = amount_in.saturating_mul(fee_factor);
    let numerator = amount_in_with_fee.saturating_mul(reserve_out);
    let denominator = reserve_in
        .saturating_mul(U256::from(PIPS_DENOM))
        .saturating_add(amount_in_with_fee);
    if denominator.is_zero() {
        return U256::ZERO;
    }
    numerator / denominator
}

/// Exact Uniswap-V2 `getAmountIn`: the input required to receive `amount_out`.
/// Returns `None` if `amount_out >= reserve_out` (unfillable).
pub fn get_amount_in(
    amount_out: U256,
    reserve_in: U256,
    reserve_out: U256,
    fee_pips: u32,
) -> Option<U256> {
    if amount_out.is_zero() || reserve_in.is_zero() || reserve_out.is_zero() {
        return None;
    }
    if amount_out >= reserve_out {
        return None;
    }
    let fee_factor = U256::from(PIPS_DENOM.saturating_sub(fee_pips.min(PIPS_DENOM)));
    let numerator = reserve_in
        .saturating_mul(amount_out)
        .saturating_mul(U256::from(PIPS_DENOM));
    let denominator = (reserve_out - amount_out).saturating_mul(fee_factor);
    if denominator.is_zero() {
        return None;
    }
    Some(numerator / denominator + U256::from(1u8))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Independent (textbook) known-answer for the canonical 0.30% fee:
    // out = amountIn*997*rOut / (rIn*1000 + amountIn*997).
    fn textbook_out(a: u128, ri: u128, ro: u128) -> u128 {
        let aif = a * 997;
        (aif * ro) / (ri * 1000 + aif)
    }

    #[test]
    fn matches_textbook_030_fee() {
        for (a, ri, ro) in [
            (1_000u128, 1_000_000u128, 2_000_000u128),
            (5_000, 10_000_000, 3_000_000),
            (1, 1_000_000_000, 1_000_000_000),
            (10u128.pow(18), 10u128.pow(21), 3 * 10u128.pow(9)),
        ] {
            let got = get_amount_out(U256::from(a), U256::from(ri), U256::from(ro), 3000);
            assert_eq!(
                got,
                U256::from(textbook_out(a, ri, ro)),
                "a={a} ri={ri} ro={ro}"
            );
        }
    }

    #[test]
    fn zero_and_edge_inputs_dont_panic() {
        let z = U256::ZERO;
        let big = U256::MAX;
        assert_eq!(get_amount_out(z, big, big, 3000), z);
        assert_eq!(get_amount_out(big, z, big, 3000), z);
        assert_eq!(get_amount_out(big, big, z, 3000), z);
        // Full-range inputs must not panic (saturating).
        let _ = get_amount_out(big, big, big, 0);
        let _ = get_amount_out(big, big, big, 999_999);
    }

    #[test]
    fn amount_in_roundtrips_approximately() {
        let (ri, ro, fee) = (U256::from(10_000_000u64), U256::from(20_000_000u64), 3000);
        let want_out = U256::from(1_000u64);
        let need_in = get_amount_in(want_out, ri, ro, fee).unwrap();
        let got_out = get_amount_out(need_in, ri, ro, fee);
        // get_amount_in rounds up, so the realized out is >= requested.
        assert!(got_out >= want_out, "got {got_out} < want {want_out}");
    }
}
