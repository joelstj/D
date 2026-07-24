//! Uniswap V4 `StateView` reads — the seed and reconcile path for V4 pools
//! (`getSlot0(poolId)` / `getLiquidity(poolId)`), analogous to V3's
//! `slot0()`/`liquidity()`.

use crate::error::{Result, V4Error};
use alloy_primitives::{Address, Bytes, B256, U256};
use l2i_core::{Blockstamp, PoolKind, Token};
use l2i_ingest::mirror::{LiveState, PoolState};
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::{PoolEntry, DYNAMIC_FEE_FLAG};
use l2i_rpc::multicall::{require_all, Call3};
use l2i_rpc::{BlockId, ChainProvider};

const GET_SLOT0: [u8; 4] = [0xc8, 0x15, 0x64, 0x1c];
const GET_LIQUIDITY: [u8; 4] = [0xfa, 0x67, 0x93, 0xd5];

/// `StateView.getSlot0(poolId)` calldata.
pub fn get_slot0_calldata(pool_id: B256) -> Bytes {
    let mut v = GET_SLOT0.to_vec();
    v.extend_from_slice(pool_id.as_slice());
    v.into()
}

/// `StateView.getLiquidity(poolId)` calldata.
pub fn get_liquidity_calldata(pool_id: B256) -> Bytes {
    let mut v = GET_LIQUIDITY.to_vec();
    v.extend_from_slice(pool_id.as_slice());
    v.into()
}

/// Decode `getSlot0` → `(sqrtPriceX96, tick, protocolFee, lpFee)`.
pub fn decode_slot0(ret: &[u8]) -> Result<(U256, i32, u32, u32)> {
    if ret.len() < 128 {
        return Err(V4Error::Decode(format!(
            "getSlot0 return too short: {}",
            ret.len()
        )));
    }
    let sqrt_price_x96 = U256::from_be_slice(&ret[0..32]);
    let tick = i32::from_be_bytes([ret[60], ret[61], ret[62], ret[63]]);
    // protocolFee / lpFee are `uint24`, right-aligned in their word. Read the low 4
    // bytes directly instead of `U256::to::<u32>()`, which *panics* on a malformed
    // word with dirty high bits — a bad RPC return must yield a decode error/skip,
    // never crash the decode thread.
    let protocol_fee = u32::from_be_bytes([ret[92], ret[93], ret[94], ret[95]]);
    let lp_fee = u32::from_be_bytes([ret[124], ret[125], ret[126], ret[127]]);
    Ok((sqrt_price_x96, tick, protocol_fee, lp_fee))
}

/// Decode `getLiquidity` → the active liquidity.
pub fn decode_liquidity(ret: &[u8]) -> Result<U256> {
    if ret.len() < 32 {
        return Err(V4Error::Decode(format!(
            "getLiquidity return too short: {}",
            ret.len()
        )));
    }
    Ok(U256::from_be_slice(&ret[0..32]))
}

fn to_core_token(chain_id: u64, t: &ValidatedToken) -> Token {
    Token::with_symbol(chain_id, t.address, t.decimals, t.symbol.clone())
}

/// The effective fee for a V4 pool: the live `lp_fee` when the pool is dynamic-fee
/// (`PoolKey.fee == 0x800000`), else the declared static fee.
pub fn effective_fee(declared_fee: u32, live_lp_fee: u32) -> u32 {
    if declared_fee == DYNAMIC_FEE_FLAG {
        live_lp_fee
    } else {
        declared_fee
    }
}

/// Seed V4 pools via a batched `getSlot0`+`getLiquidity` `StateView` multicall at
/// `block` (two calls per pool), producing `verified` mirror entries. The V4 pool
/// is emitted with `kind:"v3"` and its `poolId` identity. Non-V4 entries are ignored.
pub async fn seed_v4_pools<P: ChainProvider + ?Sized>(
    provider: &P,
    pools: &[ValidatedPool],
    state_view: Address,
    blockstamp: Blockstamp,
    block: BlockId,
) -> Result<Vec<PoolState>> {
    let v4: Vec<&ValidatedPool> = pools
        .iter()
        .filter(|p| matches!(p.entry, PoolEntry::V4(_)))
        .collect();
    if v4.is_empty() {
        return Ok(vec![]);
    }

    let mut calls = Vec::with_capacity(v4.len() * 2);
    for p in &v4 {
        let id = p
            .entry
            .identity()
            .pool_id()
            .expect("a V4 entry's identity is a poolId");
        calls.push(Call3::required(state_view, get_slot0_calldata(id)));
        calls.push(Call3::required(state_view, get_liquidity_calldata(id)));
    }

    let results = provider.multicall(calls, block).await?;
    let blobs = require_all(results)?;
    let chain_id = provider.chain_id();

    let mut out = Vec::with_capacity(v4.len());
    for (i, p) in v4.iter().enumerate() {
        let (sqrt_price_x96, tick, _protocol_fee, lp_fee) = decode_slot0(&blobs[i * 2])?;
        let liquidity = decode_liquidity(&blobs[i * 2 + 1])?;
        out.push(PoolState {
            identity: p.entry.identity(),
            kind: PoolKind::V3, // V4 maps onto the engine's v3 shape
            fee_pips: effective_fee(p.fee_pips, lp_fee),
            token0: to_core_token(chain_id, &p.token0),
            token1: to_core_token(chain_id, &p.token1),
            state: LiveState::V3 {
                sqrt_price_x96,
                tick,
                liquidity,
            },
            blockstamp: blockstamp.clone(),
            verified: true,
        });
    }
    Ok(out)
}
