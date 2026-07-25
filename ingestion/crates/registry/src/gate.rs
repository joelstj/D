//! The on-chain validation gate (`docs/ARCHITECTURE.md §7`).
//!
//! Every configured pool passes this gate — proving it exists, references the
//! declared tokens at the declared fee, has valid token metadata, comes from an
//! expected factory, and (V4) uses a safe hook — or it does not enter the live
//! set. Rejections are structured and logged loudly, never silent: a silent drop
//! reads as "covered" when it is not.

use crate::abi;
use crate::error::{GateError, RejectReason};
use crate::schema::{PoolEntry, PoolRegistry, V2V3Entry, V4Entry, DYNAMIC_FEE_FLAG};
use alloy_primitives::{Address, Bytes};
use l2i_rpc::{BlockId, ChainProvider, PrefetchProvider};
use std::collections::HashSet;

/// Validated ERC-20 (or native) token metadata, read on-chain and cached.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ValidatedToken {
    /// Token address (`0x0` for native ETH in a V4 pool).
    pub address: Address,
    /// Decimals (`0..=36`).
    pub decimals: u8,
    /// Symbol.
    pub symbol: String,
}

/// A pool that passed the gate, with its resolved token metadata.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ValidatedPool {
    /// The original registry entry.
    pub entry: PoolEntry,
    /// Resolved `token0` / `currency0`.
    pub token0: ValidatedToken,
    /// Resolved `token1` / `currency1`.
    pub token1: ValidatedToken,
    /// The fee in millionths the pool will be emitted with.
    pub fee_pips: u32,
}

/// A rejected entry and why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rejected {
    /// The entry that failed.
    pub entry: PoolEntry,
    /// Why it failed.
    pub reason: RejectReason,
}

/// The result of validating a whole registry.
#[derive(Clone, Debug, Default)]
pub struct GateOutcome {
    /// Pools that entered the live set.
    pub accepted: Vec<ValidatedPool>,
    /// Pools rejected/parked, with reasons.
    pub rejected: Vec<Rejected>,
}

/// Per-chain gate policy.
#[derive(Clone, Debug, Default)]
pub struct GatePolicy {
    /// Tokens to reject on sight (fee-on-transfer / rebasing).
    pub deny_list: HashSet<Address>,
    /// V4 hooks permitted in addition to `0x0`.
    pub safe_hooks: HashSet<Address>,
    /// When `true`, and an entry declares an expected factory, assert on-chain
    /// `factory()` equals it.
    pub check_factory: bool,
}

/// The native-ETH sentinel address used by V4 for `currency0`.
const NATIVE: Address = Address::ZERO;

/// Validate one entry at `block`. `Ok` = accepted; `Err(Rejected)` = a clean,
/// reasoned rejection. A [`GateError`] (RPC failure) is folded into a
/// [`RejectReason::Rpc`] rejection so one flaky read never aborts the whole boot.
pub async fn validate_pool<P: ChainProvider + ?Sized>(
    provider: &P,
    entry: PoolEntry,
    block: BlockId,
    policy: &GatePolicy,
) -> Result<ValidatedPool, Rejected> {
    let result = match &entry {
        PoolEntry::V2(e) => validate_v2v3(provider, e, block, policy, false).await,
        PoolEntry::V3(e) => validate_v2v3(provider, e, block, policy, true).await,
        PoolEntry::V4(e) => validate_v4(provider, e, block, policy).await,
    };
    match result {
        Ok((t0, t1, fee)) => Ok(ValidatedPool {
            entry,
            token0: t0,
            token1: t1,
            fee_pips: fee,
        }),
        Err(reason) => Err(Rejected { entry, reason }),
    }
}

/// Add a `(to, calldata)` read to the batch, skipping exact duplicates so a token
/// shared by many pools is fetched once.
fn add_call(
    calls: &mut Vec<(Address, Bytes)>,
    seen: &mut HashSet<(Address, Vec<u8>)>,
    to: Address,
    data: Bytes,
) {
    if seen.insert((to, data.to_vec())) {
        calls.push((to, data));
    }
}

/// Queue an ERC-20's `decimals()`+`symbol()` reads (native ETH has neither).
fn add_token_meta(
    calls: &mut Vec<(Address, Bytes)>,
    seen: &mut HashSet<(Address, Vec<u8>)>,
    token: Address,
) {
    if token != NATIVE {
        add_call(calls, seen, token, abi::decimals_calldata());
        add_call(calls, seen, token, abi::symbol_calldata());
    }
}

/// Validate a whole registry, logging every rejection loudly.
///
/// **Batched:** rather than one request per read per pool, this enumerates every
/// read the per-pool gate will make — `eth_getCode` for each pool, and
/// `token0`/`token1`/`fee`/`factory`/`decimals`/`symbol` `eth_call`s (deduped, so a
/// token shared across pools is read once) — pre-fetches them all in a handful of
/// batched round-trips (a single `eth_getCode` batch + chunked Multicall3
/// `aggregate3`), and then runs the *identical* [`validate_pool`] logic against an
/// offline [`PrefetchProvider`]. Same accept/reject decisions and reasons as the
/// per-call path (Phase 2 is byte-for-byte the same code), at O(1) requests instead
/// of O(pools) — so booting five chains no longer risks tripping RPC rate limits.
pub async fn validate_registry<P: ChainProvider + ?Sized>(
    provider: &P,
    registry: &PoolRegistry,
    block: BlockId,
    policy: &GatePolicy,
) -> GateOutcome {
    // Phase 1 — enumerate every read the per-pool gate needs (deduped).
    let mut code_addrs: Vec<Address> = Vec::new();
    let mut calls: Vec<(Address, Bytes)> = Vec::new();
    let mut seen: HashSet<(Address, Vec<u8>)> = HashSet::new();
    for e in &registry.pools {
        match e {
            PoolEntry::V2(x) | PoolEntry::V3(x) => {
                if !code_addrs.contains(&x.address) {
                    code_addrs.push(x.address);
                }
                add_call(&mut calls, &mut seen, x.address, abi::token0_calldata());
                add_call(&mut calls, &mut seen, x.address, abi::token1_calldata());
                if matches!(e, PoolEntry::V3(_)) {
                    add_call(&mut calls, &mut seen, x.address, abi::fee_calldata());
                }
                if policy.check_factory && x.factory.is_some() {
                    add_call(&mut calls, &mut seen, x.address, abi::factory_calldata());
                }
                add_token_meta(&mut calls, &mut seen, x.token0);
                add_token_meta(&mut calls, &mut seen, x.token1);
            }
            PoolEntry::V4(x) => {
                add_token_meta(&mut calls, &mut seen, x.currency0);
                add_token_meta(&mut calls, &mut seen, x.currency1);
            }
        }
    }

    // Fetch them all in a few batched round-trips, then serve them offline.
    let prefetched = PrefetchProvider::fetch(provider, &code_addrs, &calls, block).await;

    // Phase 2 — the exact per-pool gate, now hitting the in-memory prefetch (0 RPCs).
    let mut outcome = GateOutcome::default();
    for entry in &registry.pools {
        match validate_pool(&prefetched, entry.clone(), block, policy).await {
            Ok(v) => {
                tracing::debug!(pool = %entry.identity(), "validated");
                outcome.accepted.push(v);
            }
            Err(r) => {
                tracing::warn!(
                    pool = %r.entry.identity(),
                    reason = %r.reason,
                    "pool REJECTED by validation gate — not entering live set"
                );
                outcome.rejected.push(r);
            }
        }
    }
    tracing::info!(
        chain_id = provider.chain_id(),
        accepted = outcome.accepted.len(),
        rejected = outcome.rejected.len(),
        "validation gate complete"
    );
    outcome
}

/// Shared V2/V3 validation. `read_fee` = read and check on-chain `fee()` (V3);
/// V2 pairs expose no per-pair `fee()`, so the declared fee is a trusted protocol
/// constant there.
async fn validate_v2v3<P: ChainProvider + ?Sized>(
    provider: &P,
    e: &V2V3Entry,
    block: BlockId,
    policy: &GatePolicy,
    read_fee: bool,
) -> Result<(ValidatedToken, ValidatedToken, u32), RejectReason> {
    // 1. code exists.
    let code = provider
        .code_at(e.address, block)
        .await
        .map_err(rpc_reason)?;
    if code.is_empty() {
        return Err(RejectReason::NotAContract(e.address));
    }

    // 2/3. token0()/token1() match declared.
    let onchain_t0 = read_address(provider, e.address, abi::token0_calldata(), block).await?;
    if onchain_t0 != e.token0 {
        return Err(RejectReason::Token0Mismatch {
            declared: e.token0,
            onchain: onchain_t0,
        });
    }
    let onchain_t1 = read_address(provider, e.address, abi::token1_calldata(), block).await?;
    if onchain_t1 != e.token1 {
        return Err(RejectReason::Token1Mismatch {
            declared: e.token1,
            onchain: onchain_t1,
        });
    }

    // 4. fee (V3 only).
    let fee_pips = if read_fee {
        let ret = provider
            .call(e.address, abi::fee_calldata(), block)
            .await
            .map_err(rpc_reason)?;
        let onchain = abi::decode_fee(&ret).map_err(gate_reason)?;
        if onchain != e.fee_pips {
            return Err(RejectReason::FeeMismatch {
                declared: e.fee_pips,
                onchain,
            });
        }
        onchain
    } else {
        e.fee_pips
    };

    // 5. deny-list.
    if policy.deny_list.contains(&e.token0) {
        return Err(RejectReason::DeniedToken(e.token0));
    }
    if policy.deny_list.contains(&e.token1) {
        return Err(RejectReason::DeniedToken(e.token1));
    }

    // 6. token metadata.
    let token0 = read_token(provider, e.token0, block).await?;
    let token1 = read_token(provider, e.token1, block).await?;

    // 7. factory (optional).
    if policy.check_factory {
        if let Some(declared) = e.factory {
            let ret = provider
                .call(e.address, abi::factory_calldata(), block)
                .await
                .map_err(rpc_reason)?;
            let onchain = abi::decode_address(&ret).map_err(gate_reason)?;
            if onchain != declared {
                return Err(RejectReason::FactoryMismatch { declared, onchain });
            }
        }
    }

    Ok((token0, token1, fee_pips))
}

/// V4 validation: hook-safety, poolId consistency, token metadata. Full on-chain
/// state existence via `StateView` is verified by the V4 adapter (M6).
async fn validate_v4<P: ChainProvider + ?Sized>(
    provider: &P,
    e: &V4Entry,
    block: BlockId,
    policy: &GatePolicy,
) -> Result<(ValidatedToken, ValidatedToken, u32), RejectReason> {
    // Hook safety gate.
    if e.hooks != NATIVE && !policy.safe_hooks.contains(&e.hooks) {
        return Err(RejectReason::UnsafeHook(e.hooks));
    }

    // poolId == keccak256(abi.encode(PoolKey)).
    let computed = abi::compute_pool_id(e.currency0, e.currency1, e.fee, e.tick_spacing, e.hooks);
    if computed != e.id {
        return Err(RejectReason::PoolIdMismatch {
            declared: e.id,
            computed,
        });
    }

    // deny-list.
    if policy.deny_list.contains(&e.currency0) {
        return Err(RejectReason::DeniedToken(e.currency0));
    }
    if policy.deny_list.contains(&e.currency1) {
        return Err(RejectReason::DeniedToken(e.currency1));
    }

    // Token metadata (currency0 may be native ETH = 0x0).
    let token0 = read_token(provider, e.currency0, block).await?;
    let token1 = read_token(provider, e.currency1, block).await?;

    // A dynamic-fee pool's effective fee is read per-block by the ingestor; the
    // declared sentinel is what the registry carries.
    Ok((token0, token1, e.fee))
}

/// Read an ERC-20's decimals/symbol (or the native-ETH defaults for `0x0`).
async fn read_token<P: ChainProvider + ?Sized>(
    provider: &P,
    token: Address,
    block: BlockId,
) -> Result<ValidatedToken, RejectReason> {
    if token == NATIVE {
        return Ok(ValidatedToken {
            address: NATIVE,
            decimals: 18,
            symbol: "ETH".to_string(),
        });
    }
    let dec_ret = provider
        .call(token, abi::decimals_calldata(), block)
        .await
        .map_err(rpc_reason)?;
    let decimals = abi::decode_decimals(&dec_ret).map_err(gate_reason)?;
    if decimals > 36 {
        return Err(RejectReason::DecimalsOutOfRange { token, decimals });
    }
    let sym_ret = provider
        .call(token, abi::symbol_calldata(), block)
        .await
        .map_err(rpc_reason)?;
    let symbol = abi::decode_symbol(&sym_ret).map_err(gate_reason)?;
    Ok(ValidatedToken {
        address: token,
        decimals,
        symbol,
    })
}

async fn read_address<P: ChainProvider + ?Sized>(
    provider: &P,
    to: Address,
    calldata: alloy_primitives::Bytes,
    block: BlockId,
) -> Result<Address, RejectReason> {
    let ret = provider
        .call(to, calldata, block)
        .await
        .map_err(rpc_reason)?;
    abi::decode_address(&ret).map_err(gate_reason)
}

fn rpc_reason(e: l2i_rpc::RpcError) -> RejectReason {
    RejectReason::Rpc(e.to_string())
}

fn gate_reason(e: GateError) -> RejectReason {
    RejectReason::Rpc(e.to_string())
}

/// The set of fee values the engine can price: any static fee, plus the V4
/// dynamic-fee sentinel (read live).
pub fn is_priceable_fee(fee_pips: u32) -> bool {
    fee_pips == DYNAMIC_FEE_FLAG || fee_pips < 1_000_000
}
