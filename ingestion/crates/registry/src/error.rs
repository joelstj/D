//! Gate errors and structured rejection reasons.

use alloy_primitives::{Address, B256};

/// A hard error while *running* the gate (e.g. RPC failure) — distinct from a
/// clean rejection of an invalid entry.
#[derive(Debug, thiserror::Error)]
pub enum GateError {
    /// An RPC read failed.
    #[error("rpc: {0}")]
    Rpc(#[from] l2i_rpc::RpcError),
    /// A return value could not be decoded.
    #[error("decode: {0}")]
    Decode(String),
}

/// Why a pool entry failed validation. Every rejection is one of these, logged
/// loudly and surfaced — never a silent drop.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RejectReason {
    /// No bytecode at the pool address.
    NotAContract(Address),
    /// On-chain `token0()` ≠ declared.
    Token0Mismatch { declared: Address, onchain: Address },
    /// On-chain `token1()` ≠ declared.
    Token1Mismatch { declared: Address, onchain: Address },
    /// On-chain `fee()` ≠ declared (V3).
    FeeMismatch { declared: u32, onchain: u32 },
    /// On-chain `factory()` ≠ declared expected factory.
    FactoryMismatch { declared: Address, onchain: Address },
    /// A token's decimals were outside the valid `0..=36` range.
    DecimalsOutOfRange { token: Address, decimals: u8 },
    /// A token is on the chain's fee-on-transfer/rebasing deny-list.
    DeniedToken(Address),
    /// A V4 pool's `hooks` is neither `0x0` nor on the safe-hook allow-list.
    UnsafeHook(Address),
    /// A V4 `poolId` ≠ `keccak256(abi.encode(PoolKey))` — the declared key does
    /// not produce the declared id.
    PoolIdMismatch { declared: B256, computed: B256 },
    /// An RPC error while validating this entry.
    Rpc(String),
}

impl std::fmt::Display for RejectReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RejectReason::NotAContract(a) => write!(f, "no contract code at {a}"),
            RejectReason::Token0Mismatch { declared, onchain } => {
                write!(
                    f,
                    "token0 mismatch: declared {declared}, on-chain {onchain}"
                )
            }
            RejectReason::Token1Mismatch { declared, onchain } => {
                write!(
                    f,
                    "token1 mismatch: declared {declared}, on-chain {onchain}"
                )
            }
            RejectReason::FeeMismatch { declared, onchain } => {
                write!(f, "fee mismatch: declared {declared}, on-chain {onchain}")
            }
            RejectReason::FactoryMismatch { declared, onchain } => {
                write!(
                    f,
                    "factory mismatch: declared {declared}, on-chain {onchain}"
                )
            }
            RejectReason::DecimalsOutOfRange { token, decimals } => {
                write!(f, "token {token} decimals {decimals} out of range 0..=36")
            }
            RejectReason::DeniedToken(a) => write!(f, "token {a} is on the deny-list"),
            RejectReason::UnsafeHook(a) => write!(f, "V4 hook {a} is not 0x0 or safe-listed"),
            RejectReason::PoolIdMismatch { declared, computed } => {
                write!(
                    f,
                    "poolId mismatch: declared {declared}, computed {computed}"
                )
            }
            RejectReason::Rpc(e) => write!(f, "rpc error during validation: {e}"),
        }
    }
}
