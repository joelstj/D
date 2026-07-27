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

    use crate::event::V4SwapState;
    use alloy_primitives::B256;
    use l2i_core::{Blockstamp, PoolKind, Token};
    use l2i_ingest::mirror::{LiveState, PoolState};

    fn v4_pool(id: B256, fee_pips: u32) -> PoolState {
        PoolState {
            identity: PoolAddress::PoolId(id),
            kind: PoolKind::V3, // V4 maps onto the v3 shape
            fee_pips,
            token0: Token::with_symbol(130, Address::from([1; 20]), 18, "WETH"),
            token1: Token::with_symbol(130, Address::from([2; 20]), 6, "USDC"),
            state: LiveState::V3 {
                sqrt_price_x96: U256::from(123_456u64),
                tick: 0,
                liquidity: U256::from(1_000_000u64),
            },
            blockstamp: Blockstamp {
                chain_id: 130,
                number: 100,
                block_hash: B256::from([100; 32]),
                timestamp: 100,
            },
            verified: true,
        }
    }

    fn v4_swap(id: B256, fee: u32) -> V4SwapState {
        V4SwapState {
            pool_id: id,
            sqrt_price_x96: U256::from(654_321u64),
            liquidity: U256::from(2_000_000u64),
            tick: 5,
            fee,
        }
    }

    #[test]
    fn dynamic_fee_pool_adopts_swap_fee_on_live_path() {
        // The live ingestor now routes V4 swaps through apply_v4_swap. For a dynamic-fee
        // pool (declared_fee == 0x800000), the effective fee carried in the Swap event
        // must land in the mirror — the exact update the old apply_v3_swap path dropped.
        let mirror = Mirror::new();
        let id = B256::from([7; 32]);
        mirror.insert(v4_pool(id, 1_000)); // seeded resolved fee (0.10%)
        let stamp = Blockstamp {
            chain_id: 130,
            number: 101,
            block_hash: B256::from([101; 32]),
            timestamp: 101,
        };
        assert!(apply_v4_swap(
            &mirror,
            &v4_swap(id, 2_500),
            DYNAMIC_FEE_FLAG,
            stamp
        ));

        let got = mirror.get(&PoolAddress::PoolId(id)).unwrap();
        assert_eq!(
            got.fee_pips, 2_500,
            "dynamic-fee pool must adopt the swap's effective fee"
        );
        assert!(
            matches!(got.state, LiveState::V3 { tick: 5, .. }),
            "priced state is updated too"
        );
    }

    #[test]
    fn static_fee_pool_keeps_declared_fee_on_live_path() {
        // A static-fee pool (declared_fee != sentinel) must keep its declared fee even
        // though the Swap event carries a fee field.
        let mirror = Mirror::new();
        let id = B256::from([8; 32]);
        mirror.insert(v4_pool(id, 500)); // static 0.05%
        let stamp = Blockstamp {
            chain_id: 130,
            number: 101,
            block_hash: B256::from([101; 32]),
            timestamp: 101,
        };
        assert!(apply_v4_swap(&mirror, &v4_swap(id, 9_999), 500, stamp));

        let got = mirror.get(&PoolAddress::PoolId(id)).unwrap();
        assert_eq!(
            got.fee_pips, 500,
            "static-fee pool must not adopt the event fee"
        );
    }
}
