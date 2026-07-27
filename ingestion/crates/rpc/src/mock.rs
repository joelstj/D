//! A [`ChainProvider`] that replays recorded reads — the substrate for
//! deterministic (Tier-A) tests of anything that talks to a chain (the validation
//! gate, ingestors, gas adapters). Populate it with **recorded real** on-chain
//! responses captured at a pinned block; it then answers `call`/`code_at` from that
//! map with no network. Feature-gated (`testing`) so it never ships in prod.

use crate::error::{Result, RpcError};
use crate::frame::HeadSummary;
use crate::multicall::{decode_aggregate3_calls, encode_aggregate3_returns, Result3};
use crate::provider::{ChainProvider, HeadStream, LogStream};
use alloy::rpc::types::eth::BlockId;
use alloy::rpc::types::{Filter, Log};
use alloy_primitives::{Address, Bytes};
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// A recording-backed provider.
///
/// Beyond replaying single `(to, calldata)` reads it is **Multicall3-aware**: a
/// `multicall` (which the trait routes as an `eth_call` to `MULTICALL3`) is decoded
/// into its sub-calls and each is answered from the same recorded map, then
/// re-encoded — exactly as a real node + Multicall3 would. So one set of recorded
/// real reads serves both the per-call and the batched code paths, and a test can
/// assert the batched path yields identical results. A shared [`round_trips`] counter
/// (`Arc<AtomicUsize>`, survives cloning into tasks) records how many network
/// round-trips were issued, so tests can prove batching actually reduces requests.
///
/// [`round_trips`]: MockProvider::round_trips
#[derive(Clone, Debug, Default)]
pub struct MockProvider {
    chain_id: u64,
    block_number: u64,
    gas_price: u64,
    /// `(to, calldata) → return bytes`.
    calls: HashMap<(Address, Vec<u8>), Bytes>,
    /// `address → runtime code` (absent = no code = not a contract).
    code: HashMap<Address, Bytes>,
    /// Optional head returned by [`ChainProvider::head`].
    head: Option<HeadSummary>,
    /// Optional logs returned by [`ChainProvider::logs`].
    logs: Vec<Log>,
    /// When set, [`ChainProvider::gas_price`] returns an error — used to exercise a
    /// transient gas-read failure on the context-refresh path.
    fail_gas_price: bool,
    /// Count of network round-trips issued (shared across clones).
    round_trips: Arc<AtomicUsize>,
}

impl MockProvider {
    /// A new mock for `chain_id`.
    pub fn new(chain_id: u64) -> Self {
        Self {
            chain_id,
            ..Default::default()
        }
    }

    /// Record a `call(to, calldata) → ret`.
    pub fn with_call(
        mut self,
        to: Address,
        calldata: impl Into<Bytes>,
        ret: impl Into<Bytes>,
    ) -> Self {
        self.calls
            .insert((to, calldata.into().to_vec()), ret.into());
        self
    }

    /// Record that `addr` has contract code.
    pub fn with_code(mut self, addr: Address, code: impl Into<Bytes>) -> Self {
        self.code.insert(addr, code.into());
        self
    }

    /// Record that `addr` has code (a 1-byte non-empty marker is enough for the
    /// gate's code-exists check when the exact bytecode is irrelevant).
    pub fn with_contract(self, addr: Address) -> Self {
        self.with_code(addr, Bytes::from_static(&[0x60]))
    }

    /// Set the latest block height.
    pub fn with_block_number(mut self, n: u64) -> Self {
        self.block_number = n;
        self
    }

    /// Set the `eth_gasPrice` value returned.
    pub fn with_gas_price(mut self, wei: u64) -> Self {
        self.gas_price = wei;
        self
    }

    /// Make [`ChainProvider::gas_price`] fail, so a test can prove the context
    /// refresher retains its last-good gas price on a transient read failure instead
    /// of fabricating a `0` (which would under-cost gas → phantom profit).
    pub fn with_failing_gas_price(mut self) -> Self {
        self.fail_gas_price = true;
        self
    }

    /// Set the head returned by [`ChainProvider::head`].
    pub fn with_head(mut self, head: HeadSummary) -> Self {
        self.head = Some(head);
        self
    }

    /// Set the logs returned by [`ChainProvider::logs`].
    pub fn with_logs(mut self, logs: Vec<Log>) -> Self {
        self.logs = logs;
        self
    }

    /// How many network round-trips this mock has been asked to make. A `multicall`
    /// counts as **one** (a single `eth_call` to Multicall3), and `code_at_batch`
    /// counts as one — matching the real transports — so a test can assert that
    /// validating/reconciling N pools issues O(1) requests, not O(N).
    pub fn round_trips(&self) -> usize {
        self.round_trips.load(Ordering::SeqCst)
    }

    /// Answer one Multicall3 `aggregate3` batch from the recorded per-sub-call map:
    /// each sub-call is looked up exactly as a direct `call` would be; a hit yields
    /// `success=true` + its recorded bytes, a miss yields `success=false` (an EOA/
    /// reverted sub-call), exactly as a node would return under `allowFailure`.
    fn answer_multicall(&self, calldata: &Bytes) -> Result<Bytes> {
        let calls = decode_aggregate3_calls(calldata)?;
        let results = calls
            .into_iter()
            .map(|c| match self.calls.get(&(c.target, c.callData.to_vec())) {
                Some(ret) => Result3 {
                    success: true,
                    returnData: ret.clone(),
                },
                None => Result3 {
                    success: false,
                    returnData: Bytes::new(),
                },
            })
            .collect();
        Ok(encode_aggregate3_returns(results))
    }
}

#[async_trait]
impl ChainProvider for MockProvider {
    fn chain_id(&self) -> u64 {
        self.chain_id
    }

    async fn block_number(&self) -> Result<u64> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        Ok(self.block_number)
    }

    async fn gas_price(&self) -> Result<u64> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        if self.fail_gas_price {
            return Err(RpcError::Call("mock: gas_price read failed".into()));
        }
        Ok(self.gas_price)
    }

    async fn head(&self, _at: BlockId) -> Result<HeadSummary> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        self.head
            .clone()
            .ok_or_else(|| RpcError::Call("mock: no head recorded".into()))
    }

    async fn call(&self, to: Address, data: Bytes, _at: BlockId) -> Result<Bytes> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        // Exact recording first (covers pre-encoded aggregate3 fixtures and any
        // direct read); only then fall back to decoding a Multicall3 batch and
        // answering it from the per-sub-call recordings.
        if let Some(ret) = self.calls.get(&(to, data.to_vec())) {
            return Ok(ret.clone());
        }
        if to == l2i_chains::MULTICALL3 {
            return self.answer_multicall(&data);
        }
        Err(RpcError::Call(format!(
            "mock: no recorded call for {to} data=0x{}",
            alloy_primitives::hex::encode(&data)
        )))
    }

    async fn code_at(&self, addr: Address, _at: BlockId) -> Result<Bytes> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        Ok(self.code.get(&addr).cloned().unwrap_or_default())
    }

    async fn code_at_batch(&self, addrs: &[Address], _at: BlockId) -> Result<Vec<Bytes>> {
        // One round-trip for the whole batch — matching AlloyProvider's JSON-RPC
        // batch — so the counter reflects real request pressure.
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        Ok(addrs
            .iter()
            .map(|a| self.code.get(a).cloned().unwrap_or_default())
            .collect())
    }

    async fn logs(&self, _filter: &Filter) -> Result<Vec<Log>> {
        self.round_trips.fetch_add(1, Ordering::SeqCst);
        Ok(self.logs.clone())
    }

    async fn subscribe_heads(&self) -> Result<HeadStream> {
        Err(RpcError::Transport(
            "mock: subscriptions unsupported".into(),
        ))
    }

    async fn subscribe_logs(&self, _filter: Filter) -> Result<LogStream> {
        Err(RpcError::Transport(
            "mock: subscriptions unsupported".into(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::multicall::{require_all, Call3};

    fn a(byte: u8) -> Address {
        Address::from([byte; 20])
    }

    #[tokio::test]
    async fn multicall_answered_from_per_subcall_recordings_in_one_round_trip() {
        // Two *individual* reads are recorded; a single multicall of both must return
        // each sub-call's recorded bytes, in order — and cost exactly one round-trip.
        let mock = MockProvider::new(42161)
            .with_call(
                a(1),
                Bytes::from_static(&[0xaa]),
                Bytes::from_static(&[0x01]),
            )
            .with_call(
                a(2),
                Bytes::from_static(&[0xbb]),
                Bytes::from_static(&[0x02]),
            );

        let calls = vec![
            Call3::required(a(1), Bytes::from_static(&[0xaa])),
            Call3::required(a(2), Bytes::from_static(&[0xbb])),
        ];
        let results = mock.multicall(calls, BlockId::latest()).await.unwrap();
        let blobs = require_all(results).unwrap();

        assert_eq!(
            blobs,
            vec![Bytes::from_static(&[0x01]), Bytes::from_static(&[0x02])]
        );
        assert_eq!(
            mock.round_trips(),
            1,
            "a Multicall3 batch is one eth_call, not one per sub-call"
        );
    }

    #[tokio::test]
    async fn batched_multicall_matches_individual_calls_exactly() {
        // The batched path and the per-call path must agree byte-for-byte.
        let mock = MockProvider::new(1)
            .with_call(
                a(7),
                Bytes::from_static(&[0x11]),
                Bytes::from_static(&[0xde, 0xad]),
            )
            .with_call(
                a(8),
                Bytes::from_static(&[0x22]),
                Bytes::from_static(&[0xbe, 0xef]),
            );

        let single_7 = mock
            .call(a(7), Bytes::from_static(&[0x11]), BlockId::latest())
            .await
            .unwrap();
        let single_8 = mock
            .call(a(8), Bytes::from_static(&[0x22]), BlockId::latest())
            .await
            .unwrap();

        let batched = require_all(
            mock.multicall(
                vec![
                    Call3::required(a(7), Bytes::from_static(&[0x11])),
                    Call3::required(a(8), Bytes::from_static(&[0x22])),
                ],
                BlockId::latest(),
            )
            .await
            .unwrap(),
        )
        .unwrap();
        assert_eq!(batched, vec![single_7, single_8]);
    }

    #[tokio::test]
    async fn unrecorded_subcall_comes_back_as_a_failed_result() {
        // A missing recording models an EOA/reverted sub-call: success=false, so a
        // `require_all` caller sees a reverted batch rather than a silent wrong value.
        let mock = MockProvider::new(1);
        let results = mock
            .multicall(
                vec![Call3::required(a(9), Bytes::from_static(&[0x99]))],
                BlockId::latest(),
            )
            .await
            .unwrap();
        assert!(!results[0].success);
        assert!(matches!(
            require_all(results),
            Err(RpcError::MulticallReverted { index: 0 })
        ));
    }

    #[tokio::test]
    async fn code_at_batch_is_one_round_trip_and_reports_missing_code_as_empty() {
        let mock = MockProvider::new(1).with_contract(a(3)); // a(4) has no code
        let codes = mock
            .code_at_batch(&[a(3), a(4)], BlockId::latest())
            .await
            .unwrap();
        assert!(!codes[0].is_empty(), "a(3) has code");
        assert!(codes[1].is_empty(), "a(4) is not a contract");
        assert_eq!(
            mock.round_trips(),
            1,
            "one batched request for all addresses"
        );
    }

    #[tokio::test]
    async fn exact_recording_wins_over_multicall_dispatch() {
        // A directly recorded (MULTICALL3, calldata) blob (as the seed fixtures use)
        // is returned verbatim, not re-synthesized — preserving those tests.
        let raw = Bytes::from_static(&[0xca, 0xfe, 0xba, 0xbe]);
        let calldata = Bytes::from_static(&[0x82, 0xad, 0x56, 0xcb]); // aggregate3 selector
        let mock =
            MockProvider::new(1).with_call(l2i_chains::MULTICALL3, calldata.clone(), raw.clone());
        let got = mock
            .call(l2i_chains::MULTICALL3, calldata, BlockId::latest())
            .await
            .unwrap();
        assert_eq!(got, raw);
    }
}
