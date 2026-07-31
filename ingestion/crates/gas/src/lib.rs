//! # l2i-gas — per-chain gas & price context (`docs/ENGINE_CONTRACT.md §7`)
//!
//! Assembles each chain's [`ChainContext`]: the L2 execution `gas_price_wei`, the
//! L1 data-availability `l1_data_fee_wei` (OP-Stack `GasPriceOracle.getL1Fee`, or
//! `0` for Arbitrum whose L1 cost is folded into gas units), the config gas
//! tunables, `native_price_in`, and `hubs`.
//!
//! Lives above `rpc` (not in `chains`, which `rpc` depends on) because it performs
//! RPC reads through the [`ChainProvider`] trait.

use alloy_primitives::{Address, Bytes};
use alloy_sol_types::{sol, SolCall};
use l2i_chains::GasModel;
use l2i_core::request::ChainContext;
use l2i_rpc::{BlockId, ChainProvider};
use std::collections::BTreeMap;

sol! {
    /// OP-Stack `GasPriceOracle.getL1Fee(bytes)` — the current Ecotone/Fjord L1
    /// data fee for a representative serialized tx.
    function getL1Fee(bytes data) external view returns (uint256);
}

/// A gas-context error.
#[derive(Debug, thiserror::Error)]
pub enum GasError {
    /// An RPC read failed.
    #[error("rpc: {0}")]
    Rpc(#[from] l2i_rpc::RpcError),
    /// A return could not be decoded.
    #[error("decode: {0}")]
    Decode(String),
}

/// Config gas tunables (from `config.toml`, per chain).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GasConfig {
    /// Fixed gas for a bare arb tx.
    pub base_gas: u64,
    /// Additional gas per hop.
    pub per_hop_gas: u64,
    /// Safety multiplier on the gas estimate.
    pub gas_safety_multiplier: f64,
    /// Minimum profit in basis points to report.
    pub min_profit_bps: f64,
}

/// Calldata for `getL1Fee(sample_tx)`.
pub fn getl1fee_calldata(sample_tx: Bytes) -> Bytes {
    getL1FeeCall { data: sample_tx }.abi_encode().into()
}

/// A representative serialized transaction used to size the OP-Stack L1
/// data-availability fee. `getL1Fee` charges by *calldata bytes*, so the sample's
/// size and byte composition determine the estimate.
///
/// This is a **real recorded** EIP-1559 transaction (see the Base entry in
/// `crates/gas/tests/fixtures/gas.json`), not fabricated data — the "only real
/// data" invariant governs prices/reserves, and this is a gas-estimation sizing
/// input, exactly what the `getL1Fee` docstring calls for.
///
/// Why it matters: the runtime previously sampled `getL1Fee` with **empty**
/// calldata, i.e. the DA fee for *zero* bytes — a gross underestimate that biased
/// `net_profit` upward on the four OP-Stack chains (Base/Optimism/Unichain/Ink),
/// a phantom-profit vector (prime directive 1) the `hold_back_reason` gate can't
/// catch because it only trips on an exact `0`. Sampling a real ~53-byte tx instead
/// prices a genuine, non-zero DA cost floor (on Base this recorded sample bills
/// ~0.57 gwei vs ~0 for empty).
///
/// This sample models a minimal transfer, so a longer multi-hop arbitrage's calldata
/// bills *more* — this fixes the clear "zero bytes" bug but is still a floor, not an
/// exact per-opportunity cost. Sizing the sample to a representative arb (ideally
/// config-driven) is the tracked refinement (`docs/notes-prod-debug-enhancements.md`
/// finding B); the per-chain `gas_safety_multiplier` cushions the remaining gap.
pub fn representative_sample_tx() -> Bytes {
    // Recorded real Base transaction, hex-decoded at compile time.
    const SAMPLE_TX: &[u8] = &alloy_primitives::hex!(
        "02f8720182031e8459682f008503b9aca000825208944200000000000000000000000000000000000006\
         8806f05b59d3b2000080c0"
    );
    Bytes::from_static(SAMPLE_TX)
}

/// Decode a `getL1Fee` return into wei (saturating to `u64`).
pub fn decode_l1_fee(ret: &[u8]) -> Result<u64, GasError> {
    let v = getL1FeeCall::abi_decode_returns(ret)
        .map_err(|e| GasError::Decode(format!("getL1Fee: {e}")))?;
    Ok(u64::try_from(v).unwrap_or(u64::MAX))
}

/// Read the L2 execution gas price (`eth_gasPrice`).
pub async fn read_gas_price<P: ChainProvider + ?Sized>(provider: &P) -> Result<u64, GasError> {
    Ok(provider.gas_price().await?)
}

/// Read the L1 data fee for an OP-Stack chain via `GasPriceOracle.getL1Fee`.
pub async fn read_l1_data_fee_op_stack<P: ChainProvider + ?Sized>(
    provider: &P,
    oracle: Address,
    sample_tx: Bytes,
    block: BlockId,
) -> Result<u64, GasError> {
    let ret = provider
        .call(oracle, getl1fee_calldata(sample_tx), block)
        .await?;
    decode_l1_fee(&ret)
}

/// Read the L1 data fee for a chain, dispatching on its gas model. Arbitrum folds
/// L1 calldata cost into gas *units*, so its `l1_data_fee_wei` is `0` (covered by a
/// slightly higher `gas_safety_multiplier`).
pub async fn read_l1_data_fee<P: ChainProvider + ?Sized>(
    provider: &P,
    model: GasModel,
    oracle: Address,
    sample_tx: Bytes,
    block: BlockId,
) -> Result<u64, GasError> {
    match model {
        GasModel::OpStack => read_l1_data_fee_op_stack(provider, oracle, sample_tx, block).await,
        GasModel::Arbitrum => Ok(0),
    }
}

/// Assemble a chain's [`ChainContext`] from its live gas reads, config, and the
/// derived `native_price_in` map (already omitting no-path numeraires — see
/// [`l2i_amm::build_native_price_map`]).
#[allow(clippy::too_many_arguments)]
pub fn assemble_chain_context(
    chain_id: u64,
    gas_price_wei: u64,
    l1_data_fee_wei: u64,
    cfg: GasConfig,
    native_price_in: BTreeMap<Address, f64>,
    hubs: Vec<Address>,
) -> ChainContext {
    // Log any hub that lacks a native-price entry (it cannot be gas-costed).
    for hub in &hubs {
        if !native_price_in.contains_key(hub) {
            tracing::warn!(
                chain_id,
                hub = %hub,
                "hub has no native_price_in entry — the engine cannot gas-cost or report it"
            );
        }
    }
    ChainContext {
        chain_id,
        gas_price_wei,
        l1_data_fee_wei,
        base_gas: cfg.base_gas,
        per_hop_gas: cfg.per_hop_gas,
        gas_safety_multiplier: cfg.gas_safety_multiplier,
        min_profit_bps: cfg.min_profit_bps,
        native_price_in,
        hubs,
    }
}

/// Re-export so callers assemble the map and context from one crate.
pub use l2i_amm::build_native_price_map;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn getl1fee_selector() {
        assert_eq!(getL1FeeCall::SELECTOR, [0x49, 0x94, 0x8e, 0x0e]);
    }

    #[test]
    fn representative_sample_tx_is_a_real_nonempty_tx() {
        // Regression: the runtime used to sample getL1Fee with EMPTY calldata,
        // grossly under-costing the OP-Stack L1 data fee (phantom profit). The
        // sample must be a non-empty, real serialized tx (a typed EIP-1559 tx
        // starts with 0x02) so getL1Fee prices real calldata bytes.
        let sample = representative_sample_tx();
        assert!(!sample.is_empty(), "sample tx must not be empty");
        assert_eq!(sample.len(), 53, "the recorded real Base sample tx");
        assert_eq!(sample[0], 0x02, "an EIP-1559 typed tx envelope");
        // The getL1Fee calldata must actually embed the sample (i.e. we no longer
        // ask the oracle to price zero bytes).
        let with_sample = getl1fee_calldata(sample.clone());
        let empty = getl1fee_calldata(Bytes::new());
        assert!(with_sample.len() > empty.len());
    }
}
