//! A [`ChainProvider`] that replays recorded reads — the substrate for
//! deterministic (Tier-A) tests of anything that talks to a chain (the validation
//! gate, ingestors, gas adapters). Populate it with **recorded real** on-chain
//! responses captured at a pinned block; it then answers `call`/`code_at` from that
//! map with no network. Feature-gated (`testing`) so it never ships in prod.

use crate::error::{Result, RpcError};
use crate::frame::HeadSummary;
use crate::provider::{ChainProvider, HeadStream, LogStream};
use alloy::rpc::types::eth::BlockId;
use alloy::rpc::types::{Filter, Log};
use alloy_primitives::{Address, Bytes};
use async_trait::async_trait;
use std::collections::HashMap;

/// A recording-backed provider.
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
}

#[async_trait]
impl ChainProvider for MockProvider {
    fn chain_id(&self) -> u64 {
        self.chain_id
    }

    async fn block_number(&self) -> Result<u64> {
        Ok(self.block_number)
    }

    async fn gas_price(&self) -> Result<u64> {
        Ok(self.gas_price)
    }

    async fn head(&self, _at: BlockId) -> Result<HeadSummary> {
        self.head
            .clone()
            .ok_or_else(|| RpcError::Call("mock: no head recorded".into()))
    }

    async fn call(&self, to: Address, data: Bytes, _at: BlockId) -> Result<Bytes> {
        self.calls
            .get(&(to, data.to_vec()))
            .cloned()
            .ok_or_else(|| {
                RpcError::Call(format!(
                    "mock: no recorded call for {to} data=0x{}",
                    alloy_primitives::hex::encode(&data)
                ))
            })
    }

    async fn code_at(&self, addr: Address, _at: BlockId) -> Result<Bytes> {
        Ok(self.code.get(&addr).cloned().unwrap_or_default())
    }

    async fn logs(&self, _filter: &Filter) -> Result<Vec<Log>> {
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
