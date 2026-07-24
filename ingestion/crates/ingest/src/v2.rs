//! V2 ingestor path: startup seed via a `getReserves` multicall, then live updates
//! straight from `Sync` events (see [`crate::event`]).

use crate::error::{IngestError, Result};
use crate::mirror::{LiveState, PoolState};
use alloy_primitives::{Bytes, U256};
use l2i_core::{Blockstamp, PoolKind, Token};
use l2i_registry::gate::{ValidatedPool, ValidatedToken};
use l2i_registry::schema::PoolEntry;
use l2i_rpc::multicall::{require_all, Call3};
use l2i_rpc::{BlockId, ChainProvider};

/// `getReserves()` calldata (selector `0x0902f1ac`).
pub fn get_reserves_calldata() -> Bytes {
    Bytes::from_static(&[0x09, 0x02, 0xf1, 0xac])
}

/// Decode `getReserves() -> (uint112 reserve0, uint112 reserve1, uint32 ts)`,
/// keeping the two reserves.
pub fn decode_reserves(ret: &[u8]) -> Result<(U256, U256)> {
    if ret.len() < 64 {
        return Err(IngestError::Decode(format!(
            "getReserves return too short: {} bytes",
            ret.len()
        )));
    }
    Ok((
        U256::from_be_slice(&ret[0..32]),
        U256::from_be_slice(&ret[32..64]),
    ))
}

fn to_core_token(chain_id: u64, t: &ValidatedToken) -> Token {
    Token::with_symbol(chain_id, t.address, t.decimals, t.symbol.clone())
}

/// Seed V2 pools via a batched `getReserves` multicall at `block`, producing mirror
/// entries stamped with `blockstamp` and `verified: true` (the reads are from the
/// confirmed block). Non-V2 entries are ignored.
pub async fn seed_v2_pools<P: ChainProvider + ?Sized>(
    provider: &P,
    pools: &[ValidatedPool],
    blockstamp: Blockstamp,
    block: BlockId,
) -> Result<Vec<PoolState>> {
    let v2: Vec<&ValidatedPool> = pools
        .iter()
        .filter(|p| matches!(p.entry, PoolEntry::V2(_)))
        .collect();
    if v2.is_empty() {
        return Ok(vec![]);
    }

    let calls: Vec<Call3> = v2
        .iter()
        .map(|p| {
            let addr = p
                .entry
                .identity()
                .contract()
                .expect("a V2 entry's identity is a contract address");
            Call3::required(addr, get_reserves_calldata())
        })
        .collect();

    let results = provider.multicall(calls, block).await?;
    let blobs = require_all(results)?;
    let chain_id = provider.chain_id();

    let mut out = Vec::with_capacity(v2.len());
    for (p, blob) in v2.iter().zip(blobs) {
        let (reserve0, reserve1) = decode_reserves(&blob)?;
        out.push(PoolState {
            identity: p.entry.identity(),
            kind: PoolKind::V2,
            fee_pips: p.fee_pips,
            token0: to_core_token(chain_id, &p.token0),
            token1: to_core_token(chain_id, &p.token1),
            state: LiveState::V2 { reserve0, reserve1 },
            blockstamp: blockstamp.clone(),
            verified: true,
        });
    }
    Ok(out)
}
