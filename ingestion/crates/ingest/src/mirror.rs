//! The in-memory pool mirror — the always-resident current state of every pool.
//!
//! A sharded [`DashMap`] keyed by pool identity, so the aggregator's reads are
//! lock-cheap and the per-chain ingestor's writes don't contend. Each entry knows
//! how to render itself as the engine's [`Pool`] object.

use alloy_primitives::U256;
use dashmap::DashMap;
use l2i_core::{Blockstamp, DecU256, Pool, PoolAddress, PoolKind, Token, V2State, V3State};
use std::sync::atomic::{AtomicU64, Ordering};

/// The live, event-updated state of one pool.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LiveState {
    /// Constant-product reserves.
    V2 { reserve0: U256, reserve1: U256 },
    /// Concentrated-liquidity active-tick state.
    V3 {
        sqrt_price_x96: U256,
        tick: i32,
        liquidity: U256,
    },
}

/// A mirror entry: validated identity/metadata plus live state and blockstamp.
#[derive(Clone, Debug)]
pub struct PoolState {
    /// Pool identity (contract address or V4 poolId).
    pub identity: PoolAddress,
    /// Emitted kind (`v2`/`v3`).
    pub kind: PoolKind,
    /// Fee in millionths.
    pub fee_pips: u32,
    /// Canonical token0.
    pub token0: Token,
    /// Canonical token1.
    pub token1: Token,
    /// Live state.
    pub state: LiveState,
    /// The block this state is true at.
    pub blockstamp: Blockstamp,
    /// Honesty flag (see `docs/ENGINE_CONTRACT.md §6`).
    pub verified: bool,
}

impl PoolState {
    /// Render as the engine's [`Pool`] object.
    pub fn to_core_pool(&self) -> Pool {
        let (v2, v3) = match &self.state {
            LiveState::V2 { reserve0, reserve1 } => (
                Some(V2State {
                    reserve0: DecU256(*reserve0),
                    reserve1: DecU256(*reserve1),
                }),
                None,
            ),
            LiveState::V3 {
                sqrt_price_x96,
                tick,
                liquidity,
            } => (
                None,
                Some(V3State {
                    sqrt_price_x96: DecU256(*sqrt_price_x96),
                    tick: *tick,
                    liquidity: DecU256(*liquidity),
                }),
            ),
        };
        Pool {
            address: self.identity,
            kind: self.kind,
            fee_pips: self.fee_pips,
            verified: self.verified,
            token0: self.token0.clone(),
            token1: self.token1.clone(),
            blockstamp: self.blockstamp.clone(),
            v2,
            v3,
        }
    }

    /// Reconstruct a mirror entry from a serialized engine [`Pool`] — the inverse of
    /// [`PoolState::to_core_pool`], used to restore a persisted warm-start snapshot.
    /// Returns `None` for a malformed pool carrying neither V2 nor V3 state.
    pub fn from_core_pool(p: &Pool) -> Option<Self> {
        let state = match (&p.v2, &p.v3) {
            (Some(v2), _) => LiveState::V2 {
                reserve0: v2.reserve0.0,
                reserve1: v2.reserve1.0,
            },
            (_, Some(v3)) => LiveState::V3 {
                sqrt_price_x96: v3.sqrt_price_x96.0,
                tick: v3.tick,
                liquidity: v3.liquidity.0,
            },
            (None, None) => return None,
        };
        Some(Self {
            identity: p.address,
            kind: p.kind,
            fee_pips: p.fee_pips,
            token0: p.token0.clone(),
            token1: p.token1.clone(),
            state,
            blockstamp: p.blockstamp.clone(),
            verified: p.verified,
        })
    }
}

/// The per-chain in-memory mirror.
#[derive(Default)]
pub struct Mirror {
    pools: DashMap<PoolAddress, PoolState>,
    /// Monotonic change counter, bumped on every state mutation. The aggregator
    /// compares it against its last-sent value to tell — in O(1), without scanning
    /// the map — whether anything changed since the last request, which is what
    /// drives the `on_change`/`hybrid` cadence (`docs/ARCHITECTURE.md §8`).
    version: AtomicU64,
}

impl Mirror {
    /// A new, empty mirror.
    pub fn new() -> Self {
        Self {
            pools: DashMap::new(),
            version: AtomicU64::new(0),
        }
    }

    /// Bump the change counter. Called on every successful mutation.
    #[inline]
    fn bump(&self) {
        self.version.fetch_add(1, Ordering::Relaxed);
    }

    /// The current change-counter value. A change is detected by observing that this
    /// differs from a previously read value — never by its absolute magnitude.
    #[inline]
    pub fn version(&self) -> u64 {
        self.version.load(Ordering::Relaxed)
    }

    /// Insert or replace a pool's state (used at seed time).
    pub fn insert(&self, state: PoolState) {
        self.pools.insert(state.identity, state);
        self.bump();
    }

    /// Restore pools from a persisted warm-start snapshot. Each is inserted
    /// **`verified:false`**: a cached value is *last-known*, not confirmed at the
    /// current head, so it must never be emitted as verified until the live
    /// log/reconcile path re-derives it on-chain (`docs/ENGINE_CONTRACT.md §6`).
    /// Malformed entries are skipped. Returns how many pools were restored.
    pub fn restore(&self, pools: Vec<Pool>) -> usize {
        let mut n = 0;
        for p in pools {
            if let Some(mut s) = PoolState::from_core_pool(&p) {
                s.verified = false;
                self.pools.insert(s.identity, s);
                n += 1;
            }
        }
        if n > 0 {
            self.bump();
        }
        n
    }

    /// Clone out a pool's current state.
    pub fn get(&self, id: &PoolAddress) -> Option<PoolState> {
        self.pools.get(id).map(|e| e.clone())
    }

    /// Number of tracked pools.
    pub fn len(&self) -> usize {
        self.pools.len()
    }

    /// Number of currently-`verified` pools (cheap — counts, does not clone). Feeds
    /// the `l2i_verified_pools` gauge, the core data-quality SLI.
    pub fn verified_count(&self) -> usize {
        self.pools.iter().filter(|e| e.verified).count()
    }

    /// Whether the mirror is empty.
    pub fn is_empty(&self) -> bool {
        self.pools.is_empty()
    }

    /// Apply a decoded V2 `Sync` to a pool: update reserves and blockstamp. Returns
    /// `false` if the pool is unknown or not a V2 pool. Verified stays `true`
    /// (the reserves are event-derived from the stamped, confirmed block); a reorg
    /// or reconcile mismatch flips it via [`Mirror::set_verified`] (M9).
    pub fn apply_v2_sync(
        &self,
        id: &PoolAddress,
        reserve0: U256,
        reserve1: U256,
        blockstamp: Blockstamp,
    ) -> bool {
        match self.pools.get_mut(id) {
            Some(mut e) if matches!(e.state, LiveState::V2 { .. }) => {
                e.state = LiveState::V2 { reserve0, reserve1 };
                e.blockstamp = blockstamp;
                e.verified = true;
                drop(e);
                self.bump();
                true
            }
            _ => false,
        }
    }

    /// Apply a decoded V3 `Swap`: overwrite `sqrtPriceX96`/`tick`/`liquidity` and
    /// the blockstamp. Returns `false` if the pool is unknown or not V3.
    pub fn apply_v3_swap(
        &self,
        id: &PoolAddress,
        sqrt_price_x96: U256,
        tick: i32,
        liquidity: U256,
        blockstamp: Blockstamp,
    ) -> bool {
        match self.pools.get_mut(id) {
            Some(mut e) if matches!(e.state, LiveState::V3 { .. }) => {
                e.state = LiveState::V3 {
                    sqrt_price_x96,
                    tick,
                    liquidity,
                };
                e.blockstamp = blockstamp;
                e.verified = true;
                drop(e);
                self.bump();
                true
            }
            _ => false,
        }
    }

    /// Apply a V3 `Mint`/`Burn` in-range liquidity change: if the modified range
    /// `[tick_lower, tick_upper)` brackets the current tick, adjust the active
    /// `liquidity` by `amount` (add for `Mint`, remove for `Burn`). Blockstamp is
    /// advanced. Returns `false` if the pool is unknown or not V3.
    pub fn apply_v3_liquidity_change(
        &self,
        id: &PoolAddress,
        tick_lower: i32,
        tick_upper: i32,
        amount: U256,
        add: bool,
        blockstamp: Blockstamp,
    ) -> bool {
        match self.pools.get_mut(id) {
            Some(mut e) => {
                if let LiveState::V3 {
                    tick, liquidity, ..
                } = &mut e.state
                {
                    if tick_lower <= *tick && *tick < tick_upper {
                        *liquidity = if add {
                            liquidity.saturating_add(amount)
                        } else {
                            liquidity.saturating_sub(amount)
                        };
                    }
                    e.blockstamp = blockstamp;
                    drop(e);
                    self.bump();
                    true
                } else {
                    false
                }
            }
            None => false,
        }
    }

    /// Update a pool's fee (millionths). Used for Uniswap V4 dynamic-fee pools,
    /// whose effective fee changes per block. Returns `false` if the pool is unknown.
    pub fn set_fee_pips(&self, id: &PoolAddress, fee_pips: u32) -> bool {
        match self.pools.get_mut(id) {
            Some(mut e) => {
                e.fee_pips = fee_pips;
                drop(e);
                self.bump();
                true
            }
            None => false,
        }
    }

    /// Mark every pool whose state was last set *after* `ancestor_number`
    /// `verified:false` — the rollback step of reorg handling (`§6`). Returns how
    /// many pools were affected. They stay unverified until re-derived from
    /// canonical logs / reconciled.
    pub fn mark_unverified_after(&self, ancestor_number: u64) -> usize {
        let mut n = 0;
        for mut e in self.pools.iter_mut() {
            if e.verified && e.blockstamp.number > ancestor_number {
                e.verified = false;
                n += 1;
            }
        }
        if n > 0 {
            self.bump();
        }
        n
    }

    /// Mark **every** pool `verified:false`. Called when a chain's live loop drops
    /// (WS disconnect): until it reconnects and re-derives, none of its state can be
    /// trusted as current, so it must stop flowing to the engine. Returns how many
    /// were flipped.
    pub fn mark_all_unverified(&self) -> usize {
        let mut n = 0;
        for mut e in self.pools.iter_mut() {
            if e.verified {
                e.verified = false;
                n += 1;
            }
        }
        if n > 0 {
            self.bump();
        }
        n
    }

    /// Flip a pool's `verified` flag (reorg in flight, reconcile mismatch, re-seed).
    /// Only bumps the change counter when the flag actually changes, so a no-op
    /// set doesn't manufacture a spurious "changed" signal for the cadence.
    pub fn set_verified(&self, id: &PoolAddress, verified: bool) -> bool {
        match self.pools.get_mut(id) {
            Some(mut e) => {
                let changed = e.verified != verified;
                e.verified = verified;
                drop(e);
                if changed {
                    self.bump();
                }
                true
            }
            None => false,
        }
    }

    /// Snapshot every pool as an engine [`Pool`] object.
    pub fn snapshot(&self) -> Vec<Pool> {
        self.pools.iter().map(|e| e.to_core_pool()).collect()
    }

    /// Snapshot only the pools whose latest state is `verified`.
    pub fn snapshot_verified(&self) -> Vec<Pool> {
        self.pools
            .iter()
            .filter(|e| e.verified)
            .map(|e| e.to_core_pool())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{Address, B256};

    fn v2_state(addr: u8, r0: u64, r1: u64, number: u64) -> PoolState {
        PoolState {
            identity: PoolAddress::Contract(Address::from([addr; 20])),
            kind: PoolKind::V2,
            fee_pips: 3000,
            token0: Token::with_symbol(1, Address::from([1; 20]), 18, "A"),
            token1: Token::with_symbol(1, Address::from([2; 20]), 6, "B"),
            state: LiveState::V2 {
                reserve0: U256::from(r0),
                reserve1: U256::from(r1),
            },
            blockstamp: Blockstamp {
                chain_id: 1,
                number,
                block_hash: B256::from([number as u8; 32]),
                timestamp: number,
            },
            verified: true,
        }
    }

    #[test]
    fn version_bumps_only_on_real_mutation() {
        let m = Mirror::new();
        assert_eq!(m.version(), 0);
        let id = PoolAddress::Contract(Address::from([0xA; 20]));
        m.insert(v2_state(0xA, 1, 2, 10));
        let after_insert = m.version();
        assert!(after_insert > 0, "insert bumps the change counter");

        // A successful apply bumps.
        let stamp = Blockstamp {
            chain_id: 1,
            number: 11,
            block_hash: B256::from([11; 32]),
            timestamp: 11,
        };
        assert!(m.apply_v2_sync(&id, U256::from(3), U256::from(4), stamp.clone()));
        assert!(m.version() > after_insert, "apply bumps");
        let after_apply = m.version();

        // A no-op apply (unknown pool) does NOT bump.
        let unknown = PoolAddress::Contract(Address::from([0xF; 20]));
        assert!(!m.apply_v2_sync(&unknown, U256::from(5), U256::from(6), stamp));
        assert_eq!(m.version(), after_apply, "no-op apply must not bump");
    }

    #[test]
    fn restore_marks_unverified_and_round_trips_state() {
        // A mirror with a verified pool → its serialized snapshot → restored into a
        // fresh mirror must reproduce the priced state but as verified:false
        // (last-known, not confirmed at head).
        let src = Mirror::new();
        src.insert(v2_state(0xA, 100, 200, 500));
        let snap = src.snapshot(); // Vec<Pool>, verified:true
        assert_eq!(snap.len(), 1);
        assert!(snap[0].verified);

        let restored = Mirror::new();
        let n = restored.restore(snap.clone());
        assert_eq!(n, 1);

        // Priced state (reserves, kind, fee, identity, blockstamp) is preserved…
        let got = restored.get(&snap[0].address).unwrap().to_core_pool();
        assert_eq!(got.v2, snap[0].v2);
        assert_eq!(got.fee_pips, snap[0].fee_pips);
        assert_eq!(got.blockstamp, snap[0].blockstamp);
        // …but verified is forced false, so it is NOT emitted until re-derived.
        assert!(!got.verified, "restored pool must be verified:false");
        assert!(restored.snapshot_verified().is_empty());
    }

    #[test]
    fn from_core_pool_rejects_malformed() {
        // A pool with neither v2 nor v3 state is malformed → None.
        let mut p = v2_state(0xA, 1, 2, 3).to_core_pool();
        p.v2 = None;
        assert!(PoolState::from_core_pool(&p).is_none());
    }
}
