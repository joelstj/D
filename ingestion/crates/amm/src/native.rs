//! `native_price_in[T]` derivation (`docs/ENGINE_CONTRACT.md §7`).
//!
//! "numeraire-base-units of token `T` per 1 wei of the native gas token (ETH)",
//! derived from the WETH/T pools we already ingest — self-consistent and elegant:
//! ```text
//! price_native_in_T  = spot price of WETH quoted in T (human units)
//! native_price_in[T] = price_native_in_T · 10^(T.decimals) / 10^18
//! ```
//! A numeraire with **no** derivable price path is omitted (the engine cannot
//! gas-cost it and will never report it).

use crate::v3;
use alloy_primitives::{Address, U256};
use std::collections::BTreeMap;

/// `native_price_in[T]` from the human price of WETH in T.
pub fn native_price_in(price_native_in_t: f64, t_decimals: u8) -> f64 {
    price_native_in_t * 10f64.powi(t_decimals as i32) / 10f64.powi(18)
}

/// Human price of WETH in T from a V2 WETH/T pool's reserves.
pub fn v2_price_native_in_t(
    reserve_weth: U256,
    reserve_t: U256,
    weth_dec: u8,
    t_dec: u8,
) -> Option<f64> {
    let rw = v3::u256_to_f64(reserve_weth) / 10f64.powi(weth_dec as i32);
    let rt = v3::u256_to_f64(reserve_t) / 10f64.powi(t_dec as i32);
    if rw <= 0.0 || rt <= 0.0 {
        return None;
    }
    Some(rt / rw)
}

/// Human price of WETH in T from a V3/V4 WETH/T pool's `sqrtPriceX96`.
pub fn v3_price_native_in_t(
    sqrt_price_x96: U256,
    weth_is_token0: bool,
    weth_dec: u8,
    t_dec: u8,
) -> Option<f64> {
    let price = if weth_is_token0 {
        // price of token0(WETH) in token1(T).
        v3::sqrt_price_x96_to_price(sqrt_price_x96, weth_dec as i32, t_dec as i32)
    } else {
        // WETH is token1: invert the price of token0(T) in token1(WETH).
        let t_in_weth = v3::sqrt_price_x96_to_price(sqrt_price_x96, t_dec as i32, weth_dec as i32);
        if t_in_weth <= 0.0 {
            return None;
        }
        1.0 / t_in_weth
    };
    if price > 0.0 && price.is_finite() {
        Some(price)
    } else {
        None
    }
}

/// Build the `native_price_in` map, **omitting** any numeraire whose price could
/// not be derived. WETH itself is `1.0` (18 decimals).
pub fn build_native_price_map<I>(weth: Address, entries: I) -> BTreeMap<Address, f64>
where
    I: IntoIterator<Item = (Address, Option<f64>)>,
{
    let mut map = BTreeMap::new();
    map.insert(weth, 1.0);
    for (token, price) in entries {
        // A numeraire with no finite, positive price is silently omitted here (the
        // wiring layer logs which ones, with context); the engine then never
        // gas-costs or reports it.
        if let Some(p) = price {
            if p > 0.0 && p.is_finite() {
                map.insert(token, p);
            }
        }
    }
    map
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::address;

    #[test]
    fn weth_price_is_one() {
        assert_eq!(native_price_in(1.0, 18), 1.0);
    }

    #[test]
    fn usdc_example_matches_contract() {
        // Contract example: 1 ETH = 1873 USDC (6 dec) → 1.873e-9.
        let npi = native_price_in(1873.0, 6);
        assert!((npi - 1.873e-9).abs() < 1e-18, "got {npi}");
    }

    #[test]
    fn map_omits_missing_and_keeps_weth() {
        let weth = address!("82aF49447D8a07e3bd95BD0d56f35241523fBab1");
        let usdc = address!("af88d065e77c8cC2239327C5EDb3A432268e5831");
        let dai = address!("DA10009cBd5D07dd0CeCc66161FC93D7c9000da1");
        let map = build_native_price_map(
            weth,
            [(usdc, Some(1.9e-9)), (dai, None)], // DAI has no path → omitted
        );
        assert_eq!(map.get(&weth), Some(&1.0));
        assert!(map.contains_key(&usdc));
        assert!(!map.contains_key(&dai), "no-path numeraire must be omitted");
    }
}
