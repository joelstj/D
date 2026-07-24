//! Warm-start persistence — the "persistent memory/caching" tier.
//!
//! The [`Mirror`](crate::mirror::Mirror) is authoritative but volatile: without
//! this, every restart (deploy, crash, OOM, k8s reschedule) cold-seeds every pool
//! on every chain via a synchronous archive `eth_call` storm before the bot can
//! emit a single opportunity — minutes of blindness on a market where staleness is
//! missed money.
//!
//! This module snapshots the *verified* mirror to disk (atomically) and restores it
//! on boot. **Honesty invariant:** a restored pool is inserted `verified:false`
//! ([`Mirror::restore`](crate::mirror::Mirror::restore)) — a cached value is
//! *last-known*, never "confirmed at the current head", so it is not emitted to the
//! engine until the live log/reconcile path re-derives it on-chain
//! (`docs/ENGINE_CONTRACT.md §6`). Warm-start therefore buys an instant last-known
//! picture and skips the cold seed when the snapshot is fresh, without ever
//! asserting `verified` for data we have not re-proven.

use crate::mirror::Mirror;
use l2i_core::{Blockstamp, Pool};
use serde::{Deserialize, Serialize};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// Process-wide counter making each write's temp file unique, so two concurrent
/// flushes for the same chain (e.g. a periodic tick racing the shutdown flush) can
/// never write the *same* temp path and rename a torn file over the good snapshot.
static WRITE_SEQ: AtomicU64 = AtomicU64::new(0);

/// The persisted-snapshot schema version (bumped on any breaking layout change; a
/// mismatch is treated as "no snapshot" → cold seed).
pub const SNAPSHOT_SCHEMA_VERSION: u32 = 1;

/// A persisted point-in-time snapshot of one chain's verified mirror.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MirrorSnapshot {
    /// Layout version.
    pub schema_version: u32,
    /// The chain this snapshot belongs to.
    pub chain_id: u64,
    /// The head the snapshot was taken at (its freshness reference).
    pub head: Blockstamp,
    /// Unix seconds when written (drives the staleness check).
    pub written_at_unix: u64,
    /// The verified pools, exactly as the engine consumes them.
    pub pools: Vec<Pool>,
}

impl MirrorSnapshot {
    /// Whether this snapshot is fresh enough to warm-start from. A snapshot older
    /// than `max_staleness_secs` is discarded so the bot cold-seeds instead of
    /// resurrecting hours-old state.
    pub fn is_fresh(&self, now_unix: u64, max_staleness_secs: u64) -> bool {
        now_unix.saturating_sub(self.written_at_unix) <= max_staleness_secs
    }
}

/// On-disk path for a chain's snapshot within `dir`.
fn snapshot_path(dir: &Path, chain_id: u64) -> PathBuf {
    dir.join(format!("mirror-{chain_id}.json"))
}

/// Atomically persist `pools` (a chain's verified snapshot) to `dir`. Writes to a
/// temp file then renames onto the target, so a crash mid-write can never corrupt
/// the previous good snapshot (rename is atomic within a filesystem).
pub fn write_snapshot(
    dir: &Path,
    chain_id: u64,
    pools: &[Pool],
    head: &Blockstamp,
    written_at_unix: u64,
) -> io::Result<()> {
    std::fs::create_dir_all(dir)?;
    let snap = MirrorSnapshot {
        schema_version: SNAPSHOT_SCHEMA_VERSION,
        chain_id,
        head: head.clone(),
        written_at_unix,
        pools: pools.to_vec(),
    };
    let bytes = serde_json::to_vec(&snap).map_err(io::Error::other)?;
    let seq = WRITE_SEQ.fetch_add(1, Ordering::Relaxed);
    let tmp = dir.join(format!("mirror-{chain_id}.json.tmp.{seq}"));
    std::fs::write(&tmp, &bytes)?;
    std::fs::rename(&tmp, snapshot_path(dir, chain_id))?;
    Ok(())
}

/// Convenience: snapshot a live [`Mirror`]'s verified pools to disk.
pub fn write_mirror(
    dir: &Path,
    chain_id: u64,
    mirror: &Mirror,
    head: &Blockstamp,
    written_at_unix: u64,
) -> io::Result<()> {
    write_snapshot(
        dir,
        chain_id,
        &mirror.snapshot_verified(),
        head,
        written_at_unix,
    )
}

/// Load a chain's persisted snapshot from `dir`. `Ok(None)` when none exists or the
/// stored `schema_version`/`chain_id` does not match (treated as absent → cold
/// seed); `Err` only on an unreadable or corrupt file.
pub fn load_snapshot(dir: &Path, chain_id: u64) -> io::Result<Option<MirrorSnapshot>> {
    let path = snapshot_path(dir, chain_id);
    let bytes = match std::fs::read(&path) {
        Ok(b) => b,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let snap: MirrorSnapshot = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
    if snap.schema_version != SNAPSHOT_SCHEMA_VERSION || snap.chain_id != chain_id {
        return Ok(None);
    }
    Ok(Some(snap))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mirror::{LiveState, Mirror, PoolState};
    use alloy_primitives::{Address, B256, U256};
    use l2i_core::{PoolAddress, PoolKind, Token};

    fn tmpdir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("l2i_persist_{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    fn v2(addr: u8, r0: u64, r1: u64, number: u64) -> PoolState {
        PoolState {
            identity: PoolAddress::Contract(Address::from([addr; 20])),
            kind: PoolKind::V2,
            fee_pips: 3000,
            token0: Token::with_symbol(42161, Address::from([1; 20]), 18, "WETH"),
            token1: Token::with_symbol(42161, Address::from([2; 20]), 6, "USDC"),
            state: LiveState::V2 {
                reserve0: U256::from(r0),
                reserve1: U256::from(r1),
            },
            blockstamp: Blockstamp {
                chain_id: 42161,
                number,
                block_hash: B256::from([number as u8; 32]),
                timestamp: number,
            },
            verified: true,
        }
    }

    fn v3(addr: u8, sqrt: u128, tick: i32, liq: u128, number: u64) -> PoolState {
        PoolState {
            identity: PoolAddress::Contract(Address::from([addr; 20])),
            kind: PoolKind::V3,
            fee_pips: 500,
            token0: Token::with_symbol(42161, Address::from([1; 20]), 18, "WETH"),
            token1: Token::with_symbol(42161, Address::from([2; 20]), 6, "USDC"),
            state: LiveState::V3 {
                sqrt_price_x96: U256::from(sqrt),
                tick,
                liquidity: U256::from(liq),
            },
            blockstamp: Blockstamp {
                chain_id: 42161,
                number,
                block_hash: B256::from([number as u8; 32]),
                timestamp: number,
            },
            verified: true,
        }
    }

    #[test]
    fn round_trip_preserves_state_and_forces_unverified() {
        let dir = tmpdir("round_trip");
        let src = Mirror::new();
        src.insert(v2(0xA, 1_000_000, 2_000_000, 100));
        src.insert(v3(
            0xB,
            79_228_162_514_264_337_593_543_950_336,
            -201_243,
            42,
            100,
        ));
        let head = Blockstamp {
            chain_id: 42161,
            number: 100,
            block_hash: B256::from([100; 32]),
            timestamp: 100,
        };

        let original = {
            let mut v = src.snapshot_verified();
            v.sort_by_key(|p| p.address.to_string());
            v
        };
        assert_eq!(original.len(), 2);

        write_snapshot(&dir, 42161, &original, &head, 1_700_000_000).unwrap();

        // Reload → the bytes deserialize losslessly.
        let loaded = load_snapshot(&dir, 42161)
            .unwrap()
            .expect("snapshot present");
        assert_eq!(loaded.chain_id, 42161);
        assert_eq!(loaded.head, head);
        assert_eq!(loaded.pools.len(), 2);

        // Restore into a fresh mirror → priced state identical, but verified:false.
        let restored = Mirror::new();
        assert_eq!(restored.restore(loaded.pools), 2);
        let mut got = restored.snapshot(); // snapshot() (not _verified) — they're false now
        got.sort_by_key(|p| p.address.to_string());

        for (o, g) in original.iter().zip(got.iter()) {
            assert_eq!(g.address, o.address);
            assert_eq!(g.kind, o.kind);
            assert_eq!(g.fee_pips, o.fee_pips);
            assert_eq!(g.v2, o.v2, "V2 reserves survive the round-trip exactly");
            assert_eq!(g.v3, o.v3, "V3 slot state survives the round-trip exactly");
            assert_eq!(g.blockstamp, o.blockstamp);
            assert!(!g.verified, "restored pools must be verified:false");
        }
        // None are emittable until re-derived on-chain.
        assert!(restored.snapshot_verified().is_empty());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_snapshot_is_none_not_error() {
        let dir = tmpdir("missing");
        assert!(load_snapshot(&dir, 42161).unwrap().is_none());
    }

    #[test]
    fn schema_or_chain_mismatch_treated_as_absent() {
        let dir = tmpdir("mismatch");
        let head = Blockstamp {
            chain_id: 42161,
            number: 1,
            block_hash: B256::ZERO,
            timestamp: 1,
        };
        write_snapshot(&dir, 42161, &[], &head, 1).unwrap();
        // Asking for a different chain_id → treated as absent (no cross-chain mixup).
        assert!(load_snapshot(&dir, 8453).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn freshness_window() {
        let snap = MirrorSnapshot {
            schema_version: SNAPSHOT_SCHEMA_VERSION,
            chain_id: 1,
            head: Blockstamp {
                chain_id: 1,
                number: 1,
                block_hash: B256::ZERO,
                timestamp: 1,
            },
            written_at_unix: 1_000,
            pools: vec![],
        };
        assert!(snap.is_fresh(1_030, 60), "30s old, 60s window → fresh");
        assert!(!snap.is_fresh(1_120, 60), "120s old, 60s window → stale");
    }
}
