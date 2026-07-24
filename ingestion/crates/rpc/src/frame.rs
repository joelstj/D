//! WebSocket subscription frame decoding.
//!
//! A node pushes `eth_subscribe` updates as JSON-RPC notifications:
//! ```json
//! {"jsonrpc":"2.0","method":"eth_subscription",
//!  "params":{"subscription":"0x…","result": <header | log>}}
//! ```
//! We decode the envelope generically over the result payload `T`, so the same
//! type handles `newHeads` (→ [`HeadSummary`]) and `logs` (→ an alloy `Log`).
//!
//! [`HeadSummary`] is deliberately minimal — just the fields the ingestor's
//! blockstamping and reorg detection need (`number`, `hash`, `parent_hash`,
//! `timestamp`). It deserializes identically from a `newHeads` push and from an
//! `eth_getBlockByNumber` header, so one type serves both the live and seed paths.

use alloy_primitives::B256;
use serde::{Deserialize, Deserializer};

/// A JSON-RPC subscription notification with a typed `result` payload.
#[derive(Clone, Debug, Deserialize)]
pub struct SubscriptionNotification<T> {
    /// Always `"2.0"`.
    pub jsonrpc: String,
    /// Always `"eth_subscription"`.
    pub method: String,
    /// The subscription id and result.
    pub params: SubscriptionParams<T>,
}

impl<T> SubscriptionNotification<T> {
    /// `true` if this looks like a well-formed `eth_subscription` frame.
    pub fn is_subscription(&self) -> bool {
        self.jsonrpc == "2.0" && self.method == "eth_subscription"
    }

    /// Consume the frame, yielding `(subscription_id, result)`.
    pub fn into_parts(self) -> (String, T) {
        (self.params.subscription, self.params.result)
    }
}

/// The `params` of a subscription notification.
#[derive(Clone, Debug, Deserialize)]
pub struct SubscriptionParams<T> {
    /// The subscription id (opaque hex handle).
    pub subscription: String,
    /// The pushed payload (a header or a log).
    pub result: T,
}

/// The minimal block-header view the ingestor needs. Field names match the RPC
/// header exactly; `number`/`timestamp` arrive as hex quantities.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct HeadSummary {
    /// Block height.
    #[serde(deserialize_with = "de_quantity_u64")]
    pub number: u64,
    /// Canonical block hash.
    pub hash: B256,
    /// Parent block hash (drives reorg detection).
    #[serde(rename = "parentHash")]
    pub parent_hash: B256,
    /// Block timestamp (unix seconds).
    #[serde(deserialize_with = "de_quantity_u64")]
    pub timestamp: u64,
}

/// Deserialize an Ethereum JSON-RPC hex quantity (`"0x1a2b"`) into `u64`.
fn de_quantity_u64<'de, D: Deserializer<'de>>(d: D) -> Result<u64, D::Error> {
    let s = String::deserialize(d)?;
    let hex = s.strip_prefix("0x").unwrap_or(&s);
    u64::from_str_radix(hex, 16).map_err(serde::de::Error::custom)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::address;

    #[test]
    fn decode_real_newheads_frame() {
        let raw = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/newheads_frame_base.json"
        ))
        .unwrap();
        let frame: SubscriptionNotification<HeadSummary> = serde_json::from_str(&raw).unwrap();
        assert!(frame.is_subscription());
        let (sub, head) = frame.into_parts();
        assert!(sub.starts_with("0x"));
        // Real Base block 0x2e6cf6b captured on-chain.
        assert_eq!(head.number, 0x2e6cf6b);
        assert_eq!(head.timestamp, 0x6a57fbb9);
        assert_ne!(head.hash, head.parent_hash);
        assert_ne!(head.hash, B256::ZERO);
    }

    #[test]
    fn decode_real_logs_frame() {
        let raw = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/logs_frame_base.json"
        ))
        .unwrap();
        // Decode the log payload with alloy's rich Log type (used downstream for
        // event decoding).
        let frame: SubscriptionNotification<alloy::rpc::types::Log> =
            serde_json::from_str(&raw).unwrap();
        assert!(frame.is_subscription());
        let (_sub, log) = frame.into_parts();
        assert_eq!(
            log.address(),
            address!("4200000000000000000000000000000000000006")
        );
        assert!(log.block_number.is_some());
        assert!(!log.topics().is_empty());
    }

    #[test]
    fn quantity_decoder_handles_hex() {
        #[derive(Deserialize)]
        struct T {
            #[serde(deserialize_with = "de_quantity_u64")]
            n: u64,
        }
        let t: T = serde_json::from_str(r#"{"n":"0x1a2b"}"#).unwrap();
        assert_eq!(t.n, 0x1a2b);
        assert!(serde_json::from_str::<T>(r#"{"n":"0xzz"}"#).is_err());
    }
}
