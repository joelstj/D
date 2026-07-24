//! # l2i-chains
//!
//! Static, verified per-chain parameters for the five L2s in scope: chain id,
//! nominal block time, the L1-data-fee gas model, and the canonical predeploy /
//! precompile addresses. These are compile-time constants (the addresses are
//! stable infrastructure, verified on-chain — see `README.md`), so they never
//! come from config and never drift.
//!
//! What *does* come from config (endpoints, pools, gas tunables) lives elsewhere;
//! this crate is only the invariant facts about each chain.

use alloy_primitives::{address, Address};

/// The L1-data-fee model a chain uses, which selects the gas adapter (M7).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GasModel {
    /// OP-Stack: L1 data fee comes from the `GasPriceOracle` predeploy.
    OpStack,
    /// Arbitrum Nitro: L1 cost is folded into gas units; `l1_data_fee_wei` is 0
    /// by default, or modelled via the `ArbGasInfo` precompile.
    Arbitrum,
}

/// Multicall3, preinstalled at the same address on all five chains (OP-Stack
/// preinstall + Arbitrum deployment).
pub const MULTICALL3: Address = address!("cA11bde05977b3631167028862bE2a173976CA11");

/// OP-Stack `GasPriceOracle` predeploy (`getL1Fee`, Ecotone/Fjord L1 fee).
pub const OP_GAS_PRICE_ORACLE: Address = address!("420000000000000000000000000000000000000F");

/// OP-Stack `L1Block` predeploy (`l1BaseFee`/`blobBaseFee`).
pub const OP_L1_BLOCK: Address = address!("4200000000000000000000000000000000000015");

/// Arbitrum `ArbGasInfo` precompile (`getPricesInWei`, `getL1BaseFeeEstimate`).
pub const ARB_GAS_INFO: Address = address!("000000000000000000000000000000000000006C");

/// Static parameters for one chain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ChainSpec {
    /// Human-readable name (matches the config `[[chains]].name`).
    pub name: &'static str,
    /// EIP-155 chain id.
    pub chain_id: u64,
    /// Nominal block time in milliseconds (a hint; real timing is driven by
    /// `newHeads`).
    pub block_time_ms: u64,
    /// Which L1-data-fee model this chain uses.
    pub gas_model: GasModel,
}

impl ChainSpec {
    /// Multicall3 address for this chain (canonical on all five).
    pub const fn multicall3(&self) -> Address {
        MULTICALL3
    }

    /// The OP-Stack `GasPriceOracle`, if this chain uses the OP-Stack gas model.
    pub const fn op_gas_price_oracle(&self) -> Option<Address> {
        match self.gas_model {
            GasModel::OpStack => Some(OP_GAS_PRICE_ORACLE),
            GasModel::Arbitrum => None,
        }
    }

    /// The Arbitrum `ArbGasInfo` precompile, if this chain uses the Arbitrum gas model.
    pub const fn arb_gas_info(&self) -> Option<Address> {
        match self.gas_model {
            GasModel::Arbitrum => Some(ARB_GAS_INFO),
            GasModel::OpStack => None,
        }
    }
}

/// Arbitrum One.
pub const ARBITRUM: ChainSpec = ChainSpec {
    name: "arbitrum",
    chain_id: 42161,
    block_time_ms: 250,
    gas_model: GasModel::Arbitrum,
};

/// Base.
pub const BASE: ChainSpec = ChainSpec {
    name: "base",
    chain_id: 8453,
    block_time_ms: 2000,
    gas_model: GasModel::OpStack,
};

/// Optimism.
pub const OPTIMISM: ChainSpec = ChainSpec {
    name: "optimism",
    chain_id: 10,
    block_time_ms: 2000,
    gas_model: GasModel::OpStack,
};

/// Unichain (Uniswap V4 liquidity venue).
pub const UNICHAIN: ChainSpec = ChainSpec {
    name: "unichain",
    chain_id: 130,
    block_time_ms: 1000,
    gas_model: GasModel::OpStack,
};

/// Ink.
pub const INK: ChainSpec = ChainSpec {
    name: "ink",
    chain_id: 57073,
    block_time_ms: 1000,
    gas_model: GasModel::OpStack,
};

/// All five chains in scope, in canonical order.
pub const ALL: [ChainSpec; 5] = [ARBITRUM, BASE, OPTIMISM, UNICHAIN, INK];

/// Look up a [`ChainSpec`] by chain id.
pub fn by_id(chain_id: u64) -> Option<ChainSpec> {
    ALL.into_iter().find(|c| c.chain_id == chain_id)
}

/// Look up a [`ChainSpec`] by config name (case-insensitive).
pub fn by_name(name: &str) -> Option<ChainSpec> {
    ALL.into_iter().find(|c| c.name.eq_ignore_ascii_case(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_five_chains_have_expected_ids() {
        assert_eq!(ARBITRUM.chain_id, 42161);
        assert_eq!(BASE.chain_id, 8453);
        assert_eq!(OPTIMISM.chain_id, 10);
        assert_eq!(UNICHAIN.chain_id, 130);
        assert_eq!(INK.chain_id, 57073);
        assert_eq!(ALL.len(), 5);
    }

    #[test]
    fn gas_models_are_correct() {
        // Only Arbitrum uses the folded-L1 (ArbGasInfo) model; the rest are OP-Stack.
        assert_eq!(ARBITRUM.gas_model, GasModel::Arbitrum);
        for c in [BASE, OPTIMISM, UNICHAIN, INK] {
            assert_eq!(
                c.gas_model,
                GasModel::OpStack,
                "{} should be OP-Stack",
                c.name
            );
        }
    }

    #[test]
    fn predeploy_accessors_match_gas_model() {
        assert_eq!(ARBITRUM.arb_gas_info(), Some(ARB_GAS_INFO));
        assert_eq!(ARBITRUM.op_gas_price_oracle(), None);
        assert_eq!(BASE.op_gas_price_oracle(), Some(OP_GAS_PRICE_ORACLE));
        assert_eq!(BASE.arb_gas_info(), None);
        for c in ALL {
            assert_eq!(c.multicall3(), MULTICALL3);
        }
    }

    #[test]
    fn lookup_by_id_and_name() {
        assert_eq!(by_id(8453), Some(BASE));
        assert_eq!(by_id(999999), None);
        assert_eq!(by_name("UniChain"), Some(UNICHAIN));
        assert_eq!(by_name("nope"), None);
    }

    #[test]
    fn ids_and_names_are_unique() {
        for (i, a) in ALL.iter().enumerate() {
            for b in &ALL[i + 1..] {
                assert_ne!(a.chain_id, b.chain_id);
                assert_ne!(a.name, b.name);
            }
        }
    }
}
