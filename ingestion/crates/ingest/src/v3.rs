//! V3 ingestor path: startup seed via a `slot0()`+`liquidity()` multicall, then
//! live updates from `Swap` events, with `Mint`/`Burn` refreshing in-range
//! liquidity (see [`crate::event`]).

use crate::error::{IngestError, Result};
use crate::mirror::{LiveState, PoolState};
use alloy_primitives::{Bytes, U256};
use l2i_core::{Blockstamp, PoolKind, Token};
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::PoolEntry;
use l2i_rpc::multicall::{require_all, Call3};
use l2i_rpc::{BlockId, ChainProvider};

/// `slot0()` calldata (selector `0x3850c7bd`).
pub fn slot0_calldata() -> Bytes {
    Bytes::from_static(&[0x38, 0x50, 0xc7, 0xbd])
}

/// `liquidity()` calldata (selector `0x1a686502`).
pub fn liquidity_calldata() -> Bytes {
    Bytes::from_static(&[0x1a, 0x68, 0x65, 0x02])
}

/// Decode `slot0()` → `(sqrtPriceX96, tick)` (the rest of the tuple is ignored).
pub fn decode_slot0(ret: &[u8]) -> Result<(U256, i32)> {
    if ret.len() < 64 {
        return Err(IngestError::Decode(format!(
            "slot0 return too short: {}",
            ret.len()
        )));
    }
    let sqrt_price_x96 = U256::from_be_slice(&ret[0..32]);
    // tick is an int24 sign-extended into word 1.
    let tick = i32::from_be_bytes([ret[60], ret[61], ret[62], ret[63]]);
    Ok((sqrt_price_x96, tick))
}

/// Decode `liquidity()` → the active in-range liquidity.
pub fn decode_liquidity(ret: &[u8]) -> Result<U256> {
    if ret.len() < 32 {
        return Err(IngestError::Decode(format!(
            "liquidity return too short: {}",
            ret.len()
        )));
    }
    Ok(U256::from_be_slice(&ret[0..32]))
}

fn to_core_token(chain_id: u64, t: &ValidatedToken) -> Token {
    Token::with_symbol(chain_id, t.address, t.decimals, t.symbol.clone())
}

/// Seed V3 pools via a batched `slot0()`+`liquidity()` multicall at `block`
/// (two calls per pool, in that order), producing `verified` mirror entries.
/// Non-V3 entries are ignored.
pub async fn seed_v3_pools<P: ChainProvider + ?Sized>(
    provider: &P,
    pools: &[ValidatedPool],
    blockstamp: Blockstamp,
    block: BlockId,
) -> Result<Vec<PoolState>> {
    let v3: Vec<&ValidatedPool> = pools
        .iter()
        .filter(|p| matches!(p.entry, PoolEntry::V3(_)))
        .collect();
    if v3.is_empty() {
        return Ok(vec![]);
    }

    let mut calls = Vec::with_capacity(v3.len() * 2);
    for p in &v3 {
        let addr = p
            .entry
            .identity()
            .contract()
            .expect("a V3 entry's identity is a contract address");
        calls.push(Call3::required(addr, slot0_calldata()));
        calls.push(Call3::required(addr, liquidity_calldata()));
    }

    let results = provider.multicall(calls, block).await?;
    let blobs = require_all(results)?;
    let chain_id = provider.chain_id();

    let mut out = Vec::with_capacity(v3.len());
    for (i, p) in v3.iter().enumerate() {
        let (sqrt_price_x96, tick) = decode_slot0(&blobs[i * 2])?;
        let liquidity = decode_liquidity(&blobs[i * 2 + 1])?;
        out.push(PoolState {
            identity: p.entry.identity(),
            kind: PoolKind::V3,
            fee_pips: p.fee_pips,
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
