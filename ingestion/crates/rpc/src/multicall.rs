//! Multicall3 (`0xcA11…76CA11`) request encoding and response decoding.
//!
//! We batch startup seeding and reconciliation reads through `aggregate3`, which
//! takes `(address target, bool allowFailure, bytes callData)[]` and returns
//! `(bool success, bytes returnData)[]`. Only the ABI *types* determine the
//! selector and layout, so the Rust struct names here are ours; the wire encoding
//! matches Multicall3 exactly. Verified against a recorded on-chain response in
//! `tests/`.

use crate::error::{Result, RpcError};
use alloy_primitives::{Address, Bytes};
use alloy_sol_types::{sol, SolCall};

sol! {
    /// One sub-call: target, whether a revert is tolerated, and the calldata.
    #[derive(Debug, PartialEq, Eq)]
    struct Call3 {
        address target;
        bool allowFailure;
        bytes callData;
    }

    /// One sub-result: whether it succeeded, and the raw return bytes.
    #[derive(Debug, PartialEq, Eq)]
    struct Result3 {
        bool success;
        bytes returnData;
    }

    /// `Multicall3.aggregate3` — the only entrypoint we use.
    function aggregate3(Call3[] calls) external payable returns (Result3[] returnData);
}

impl Call3 {
    /// A sub-call that must succeed (a revert aborts the whole batch semantics
    /// only if the caller treats `success=false` as fatal — see [`require_all`]).
    pub fn required(target: Address, call_data: impl Into<Bytes>) -> Self {
        Self {
            target,
            allowFailure: false,
            callData: call_data.into(),
        }
    }

    /// A sub-call whose revert is tolerated (`success=false` returned, batch ok).
    pub fn allow_failure(target: Address, call_data: impl Into<Bytes>) -> Self {
        Self {
            target,
            allowFailure: true,
            callData: call_data.into(),
        }
    }
}

/// ABI-encode an `aggregate3(calls)` call into calldata.
pub fn encode_aggregate3(calls: Vec<Call3>) -> Bytes {
    aggregate3Call { calls }.abi_encode().into()
}

/// Decode the `Result3[]` returned by an `aggregate3` call.
pub fn decode_aggregate3(return_data: &[u8]) -> Result<Vec<Result3>> {
    aggregate3Call::abi_decode_returns(return_data)
        .map_err(|e| RpcError::Decode(format!("aggregate3 return: {e}")))
}

/// Decode `aggregate3` results and require every sub-call to have succeeded,
/// returning just the return-data blobs in order.
pub fn require_all(results: Vec<Result3>) -> Result<Vec<Bytes>> {
    let mut out = Vec::with_capacity(results.len());
    for (index, r) in results.into_iter().enumerate() {
        if !r.success {
            return Err(RpcError::MulticallReverted { index });
        }
        out.push(r.returnData);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::hex;

    // Selectors of Multicall3's own view helpers (used as sub-calls in fixtures).
    const GET_CURRENT_BLOCK_TIMESTAMP: [u8; 4] = [0x0f, 0x28, 0xc9, 0x7d];
    const GET_BLOCK_NUMBER: [u8; 4] = [0x42, 0xcb, 0xb1, 0x5c];

    #[test]
    fn aggregate3_selector_is_canonical() {
        // Multicall3.aggregate3((address,bool,bytes)[]) selector = 0x82ad56cb.
        assert_eq!(aggregate3Call::SELECTOR, [0x82, 0xad, 0x56, 0xcb]);
    }

    #[test]
    fn encode_decode_roundtrip() {
        let mc = l2i_chains::MULTICALL3;
        let calls = vec![
            Call3::required(mc, Bytes::from_static(&GET_CURRENT_BLOCK_TIMESTAMP)),
            Call3::required(mc, Bytes::from_static(&GET_BLOCK_NUMBER)),
        ];
        let data = encode_aggregate3(calls.clone());
        // Re-decode the *call* to prove the encoding is well-formed.
        let decoded = aggregate3Call::abi_decode(&data).unwrap();
        assert_eq!(decoded.calls, calls);
        // Selector prefix.
        assert_eq!(&data[..4], &aggregate3Call::SELECTOR);
    }

    #[test]
    fn require_all_flags_reverts() {
        let results = vec![
            Result3 {
                success: true,
                returnData: Bytes::from_static(&[1]),
            },
            Result3 {
                success: false,
                returnData: Bytes::new(),
            },
        ];
        match require_all(results) {
            Err(RpcError::MulticallReverted { index }) => assert_eq!(index, 1),
            other => panic!("expected revert at #1, got {other:?}"),
        }
    }

    #[test]
    fn decode_rejects_garbage() {
        assert!(decode_aggregate3(&hex!("deadbeef")).is_err());
    }
}
