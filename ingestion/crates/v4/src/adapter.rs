//! The live V4 adapter glue: hook-safety and applying `Swap`/`ModifyLiquidity` to
//! the shared mirror (V4 pools live in the same [`Mirror`] as V2/V3, emitted as
//! `kind:"v3"` keyed by `poolId`).

use crate::event::{V4LiquidityChange, V4SwapState};
use alloy_primitives::{Address, U256};
use l2i_core::{Blockstamp, PoolAddress};
use l2i_ingest::mirror::Mirror;
use l2i_registry::schema::DYNAMIC_FEE_FLAG;
use std::collections::HashSet;

/// A V4 pool's hook is safe iff it is the zero address or on the per-chain
/// safe-hook allow-list. Any other hook can alter swap accounting and would make
/// our `v3` pricing wrong (`docs/ENGINE_CONTRACT.md §4`).
pub fn hook_is_safe(hooks: Address, safe_hooks: &HashSet<Address>) -> bool {
    hooks == Address::ZERO || safe_hooks.contains(&hooks)
}

/// Apply a decoded V4 `Swap` to the mirror: update the `v3`-shaped state and, for a
/// dynamic-fee pool, the effective fee read from the event. `declared_fee` is the
/// registry `PoolKey.fee` (the `0x800000` sentinel marks dynamic). Returns `false`
/// if the pool is unknown.
pub fn apply_v4_swap(
    mirror: &Mirror,
    swap: &V4SwapState,
    declared_fee: u32,
    blockstamp: Blockstamp,
) -> bool {
    let id = PoolAddress::PoolId(swap.pool_id);
    let updated = mirror.apply_v3_swap(
        &id,
        swap.sqrt_price_x96,
        swap.tick,
        swap.liquidity,
        blockstamp,
    );
    if updated && declared_fee == DYNAMIC_FEE_FLAG {
        mirror.set_fee_pips(&id, swap.fee);
    }
    updated
}

/// Apply a V4 `ModifyLiquidity` to the mirror: if the modified range brackets the
/// current tick, adjust active liquidity by the signed delta. Returns `false` if
/// the pool is unknown or not `v3`-shaped.
pub fn apply_v4_modify_liquidity(
    mirror: &Mirror,
    change: &V4LiquidityChange,
    blockstamp: Blockstamp,
) -> bool {
    let id = PoolAddress::PoolId(change.pool_id);
    let (amount, add) = if change.liquidity_delta.is_negative() {
        (
            U256::from_be_bytes::<32>((-change.liquidity_delta).to_be_bytes()),
            false,
        )
    } else {
        (
            U256::from_be_bytes::<32>(change.liquidity_delta.to_be_bytes()),
            true,
        )
    };
    mirror.apply_v3_liquidity_change(
        &id,
        change.tick_lower,
        change.tick_upper,
        amount,
        add,
        blockstamp,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::address;

    #[test]
    fn hook_gate_accepts_zero_rejects_unknown() {
        let mut safe = HashSet::new();
        let ok_hook = address!("2ea5eac8a4e31f0889e86fc135c5eae8e0b16ae0");
        assert!(hook_is_safe(Address::ZERO, &safe)); // 0x0 accepted
        assert!(!hook_is_safe(ok_hook, &safe)); // unknown rejected
        safe.insert(ok_hook);
        assert!(hook_is_safe(ok_hook, &safe)); // safe-listed accepted
    }

    #[test]
    fn effective_fee_dynamic_vs_static() {
        use crate::stateview::effective_fee;
        assert_eq!(effective_fee(500, 9999), 500); // static ignores live
        assert_eq!(effective_fee(DYNAMIC_FEE_FLAG, 2500), 2500); // dynamic uses live
    }
}
