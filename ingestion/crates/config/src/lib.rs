//! # l2i-config — the plug-and-play configuration surface
//!
//! One `config.toml` is the entire integration surface (`docs/ARCHITECTURE.md
//! §10`): chains, endpoints, pool registries, gas params, cross-chain wiring, the
//! engine URL, and the output sink. Copy the example, fill endpoints, run.
//!
//! User-fillable addresses (hubs, numeraires, pool references) are kept as
//! `String` here and **validated on-chain later** by the M2 gate — the config load
//! checks *structure*, the gate checks *reality*. Only the stable infra addresses
//! are parsed as typed [`Address`](alloy_primitives::Address) at load time.

use alloy_primitives::Address;
use l2i_aggregator::cadence::CadenceMode;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

/// The wire-contract version this build understands. A config declaring a different
/// `schema_version` is rejected rather than silently run against incompatible code.
pub const SUPPORTED_SCHEMA_VERSION: u32 = 1;

/// The whole config file.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Config {
    /// Wire-contract version this config targets.
    pub schema_version: u32,
    /// The detection engine.
    pub engine: EngineConfig,
    /// When to send requests.
    pub cadence: CadenceConfig,
    /// Outbound fan-out.
    pub output: OutputConfig,
    /// Health/metrics/logs.
    pub observability: ObservabilityConfig,
    /// Stable infra addresses.
    pub infra: InfraConfig,
    /// Per-chain config.
    pub chains: Vec<ChainConfig>,
    /// Optional cross-chain wiring.
    #[serde(default)]
    pub cross_chain: Option<CrossChainConfig>,
    /// Warm-start persistence cache (off unless configured).
    #[serde(default)]
    pub cache: CacheConfig,
}

/// `[cache]` — the warm-start persistence tier. When `enabled`, the verified mirror
/// is snapshotted to `dir` periodically and on shutdown, and restored on boot (as
/// `verified:false`) to skip the cold-seed RPC storm when the snapshot is fresh.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CacheConfig {
    /// Whether warm-start persistence is on.
    #[serde(default)]
    pub enabled: bool,
    /// Directory for per-chain snapshot files.
    #[serde(default = "default_cache_dir")]
    pub dir: String,
    /// How often to flush a snapshot while running (ms).
    #[serde(default = "default_snapshot_interval_ms")]
    pub snapshot_interval_ms: u64,
    /// Discard a snapshot older than this on boot (seconds) — a stale snapshot
    /// cold-seeds instead of resurrecting hours-old state.
    #[serde(default = "default_max_staleness_secs")]
    pub max_staleness_secs: u64,
}

impl Default for CacheConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            dir: default_cache_dir(),
            snapshot_interval_ms: default_snapshot_interval_ms(),
            max_staleness_secs: default_max_staleness_secs(),
        }
    }
}

fn default_cache_dir() -> String {
    "cache".to_string()
}
fn default_snapshot_interval_ms() -> u64 {
    30_000
}
fn default_max_staleness_secs() -> u64 {
    120
}

/// `[engine]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EngineConfig {
    /// `"http"` (default) | `"subprocess"`.
    pub transport: String,
    /// Long-running FastAPI base URL.
    pub http_url: String,
    /// Subprocess command (used when `transport = "subprocess"`).
    #[serde(default)]
    pub subprocess_cmd: Option<String>,
    /// Health path.
    pub health_path: String,
    /// Detect path.
    pub detect_path: String,
    /// Top-N opportunities.
    pub top_n: u32,
    /// Max hops (2..=8).
    pub max_hops: u32,
    /// Per-call timeout (ms).
    pub call_timeout_ms: u64,
    /// Persistent connection pool.
    #[serde(default = "default_true")]
    pub keep_alive: bool,
    /// Whether the first request of a session is incremental (should be false).
    #[serde(default)]
    pub first_request_incremental: bool,
}

/// `[cadence]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CadenceConfig {
    /// `on_change` | `interval` | `hybrid`.
    pub mode: CadenceMode,
    /// Debounce floor (ms).
    pub min_interval_ms: u64,
    /// Heartbeat ceiling (ms).
    pub max_interval_ms: u64,
    /// Send only changed pools after the first request.
    #[serde(default = "default_true")]
    pub incremental: bool,
}

/// `[output]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct OutputConfig {
    /// `ws` (default) | `stdout` | `redis` | `grpc`.
    pub sink: String,
    /// WS bind address.
    #[serde(default)]
    pub ws_bind: Option<String>,
    /// Redis URL.
    #[serde(default)]
    pub redis_url: Option<String>,
    /// gRPC bind address.
    #[serde(default)]
    pub grpc_bind: Option<String>,
}

/// `[observability]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ObservabilityConfig {
    /// Health bind.
    pub health_bind: String,
    /// Prometheus metrics bind.
    pub metrics_bind: String,
    /// `trace|debug|info|warn|error`.
    pub log_level: String,
    /// `json` | `pretty`.
    pub log_format: String,
}

/// `[infra]` — stable, on-chain-verified addresses (typed).
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct InfraConfig {
    /// Multicall3.
    pub multicall3: Address,
    /// OP-Stack GasPriceOracle.
    pub op_gas_price_oracle: Address,
    /// OP-Stack L1Block.
    pub op_l1_block: Address,
    /// Arbitrum ArbGasInfo.
    pub arb_gas_info: Address,
}

/// One `[[chains]]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ChainConfig {
    /// Name (matches `l2i_chains`).
    pub name: String,
    /// Chain id.
    pub chain_id: u64,
    /// Whether to run this chain.
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// WebSocket endpoint (hot path).
    pub ws_url: String,
    /// HTTP archive endpoint (seed/reconcile).
    pub http_url: String,
    /// Nominal block time hint (ms).
    pub block_time_ms: u64,
    /// `op_stack` | `arbitrum`.
    pub gas_model: String,
    /// Flashblocks pre-confirmations (M10, off by default).
    #[serde(default)]
    pub flashblocks: bool,
    /// Minimum profit bps.
    pub min_profit_bps: f64,
    /// Fixed gas.
    pub base_gas: u64,
    /// Per-hop gas.
    pub per_hop_gas: u64,
    /// Gas safety multiplier.
    pub gas_safety_multiplier: f64,
    /// Reconcile interval (ms).
    pub reconcile_interval_ms: u64,
    /// Hub token addresses (validated later).
    #[serde(default)]
    pub hubs: Vec<String>,
    /// Numeraire token addresses (validated later).
    #[serde(default)]
    pub numeraires: Vec<String>,
    /// Wrapped-native (WETH) address on this chain. When set, native-price derivation
    /// *verifies* that each `native_price_pools` entry actually pairs the numeraire
    /// with WETH — a pool that doesn't is omitted rather than mispriced. When unset,
    /// derivation falls back to cross-pool agreement.
    #[serde(default)]
    pub weth: Option<String>,
    /// Path to this chain's pool registry.
    pub pool_registry: String,
    /// `numeraire → WETH/numeraire pool` used to price ETH in it.
    #[serde(default)]
    pub native_price_pools: BTreeMap<String, String>,
    /// V4 PoolManager (V4 chains).
    #[serde(default)]
    pub uniswap_v4_pool_manager: Option<String>,
    /// V4 StateView (V4 chains).
    #[serde(default)]
    pub uniswap_v4_state_view: Option<String>,
    /// Safe-hook allow-list (V4).
    #[serde(default)]
    pub safe_hooks: Vec<String>,
}

/// `[cross_chain]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CrossChainConfig {
    /// Whether cross-chain detection is on.
    #[serde(default)]
    pub enabled: bool,
    /// `(asset, numeraire)` symbol pairs.
    #[serde(default)]
    pub pairs: Vec<[String; 2]>,
    /// Canonical assets.
    #[serde(default)]
    pub assets: Vec<CrossChainAsset>,
    /// Bridge routes.
    #[serde(default)]
    pub bridges: Vec<CrossChainBridge>,
}

/// A `[[cross_chain.assets]]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CrossChainAsset {
    /// Canonical symbol.
    pub symbol: String,
    /// Per-chain representations.
    pub representations: Vec<CrossChainRep>,
}

/// One representation.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CrossChainRep {
    /// Chain id.
    pub chain_id: u64,
    /// Token address (validated later).
    pub address: String,
    /// Decimals.
    pub decimals: u8,
    /// Native representation on that chain.
    #[serde(default)]
    pub native: bool,
    /// A configured bridge can move it.
    #[serde(default)]
    pub bridgeable: bool,
}

/// One `[[cross_chain.bridges]]`.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CrossChainBridge {
    /// Asset symbol.
    pub symbol: String,
    /// Source chain.
    pub from_chain: u64,
    /// Destination chain.
    pub to_chain: u64,
    /// Proportional fee bps.
    pub fee_bps: f64,
    /// Fixed fee (base units).
    pub fixed_fee: u64,
    /// Settlement seconds.
    pub settle_seconds: u64,
}

fn default_true() -> bool {
    true
}

/// Map a config `gas_model` string to the registry [`GasModel`](l2i_chains::GasModel).
fn parse_gas_model(s: &str) -> Option<l2i_chains::GasModel> {
    match s {
        "op_stack" => Some(l2i_chains::GasModel::OpStack),
        "arbitrum" => Some(l2i_chains::GasModel::Arbitrum),
        _ => None,
    }
}

/// Whether `s` is an absolute `http(s)://` URL (a bare host or empty string is not).
fn is_absolute_http_url(s: &str) -> bool {
    let s = s.trim();
    (s.starts_with("http://") && s.len() > "http://".len())
        || (s.starts_with("https://") && s.len() > "https://".len())
}

/// A config error.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    /// The file could not be read.
    #[error("reading config {path}: {source}")]
    Io {
        /// Path.
        path: String,
        /// Cause.
        source: std::io::Error,
    },
    /// The file was not valid config TOML.
    #[error("parsing config {path}: {source}")]
    Parse {
        /// Path.
        path: String,
        /// Cause.
        source: toml::de::Error,
    },
    /// A structural validation failed.
    #[error("invalid config: {0}")]
    Invalid(String),
}

impl Config {
    /// Parse from TOML text.
    pub fn parse(src: &str) -> Result<Self, toml::de::Error> {
        toml::from_str(src)
    }

    /// Load and parse a config file.
    pub fn load(path: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let path = path.as_ref();
        let src = std::fs::read_to_string(path).map_err(|e| ConfigError::Io {
            path: path.display().to_string(),
            source: e,
        })?;
        Self::parse(&src).map_err(|e| ConfigError::Parse {
            path: path.display().to_string(),
            source: e,
        })
    }

    /// Structural + cross-field validation (not on-chain). This is what makes
    /// `l2-ingest --check-config` an authoritative pre-flight: every footgun that
    /// would otherwise surface only at runtime (or silently degrade the bot) is
    /// caught here — unsupported schema, unknown/mis-modelled chains, empty or
    /// malformed endpoints, sink misconfig, and out-of-range intervals/timeouts.
    pub fn validate(&self) -> Result<(), ConfigError> {
        let invalid = |m: String| Err(ConfigError::Invalid(m));

        if self.schema_version != SUPPORTED_SCHEMA_VERSION {
            return invalid(format!(
                "unsupported schema_version {} (this build supports {})",
                self.schema_version, SUPPORTED_SCHEMA_VERSION
            ));
        }
        if self.chains.is_empty() {
            return invalid("no [[chains]] configured".into());
        }
        if !(2..=8).contains(&self.engine.max_hops) {
            return invalid(format!(
                "engine.max_hops {} out of range 2..=8",
                self.engine.max_hops
            ));
        }
        if self.engine.call_timeout_ms == 0 {
            return invalid("engine.call_timeout_ms must be > 0".into());
        }
        match self.engine.transport.as_str() {
            "http" => {
                if !is_absolute_http_url(&self.engine.http_url) {
                    return invalid(format!(
                        "engine.http_url '{}' must be an absolute http(s):// URL",
                        self.engine.http_url
                    ));
                }
            }
            "subprocess" => {
                if self
                    .engine
                    .subprocess_cmd
                    .as_deref()
                    .unwrap_or("")
                    .is_empty()
                {
                    return invalid(
                        "engine.subprocess_cmd is required when transport = subprocess".into(),
                    );
                }
            }
            other => {
                return invalid(format!(
                    "engine.transport '{other}' must be http|subprocess"
                ))
            }
        }

        // Cadence bounds: a positive debounce floor no larger than the heartbeat
        // ceiling (a zero would busy-loop; min > max would make on-change never fire
        // before the heartbeat).
        if self.cadence.min_interval_ms == 0 {
            return invalid("cadence.min_interval_ms must be > 0".into());
        }
        if self.cadence.min_interval_ms > self.cadence.max_interval_ms {
            return invalid(format!(
                "cadence.min_interval_ms ({}) must be <= max_interval_ms ({})",
                self.cadence.min_interval_ms, self.cadence.max_interval_ms
            ));
        }

        // Output sink + the bind/url the chosen sink needs.
        match self.output.sink.as_str() {
            "ws" => {
                if self.output.ws_bind.as_deref().unwrap_or("").is_empty() {
                    return invalid("output.ws_bind is required for sink = ws".into());
                }
            }
            "redis" => {
                if self.output.redis_url.as_deref().unwrap_or("").is_empty() {
                    return invalid("output.redis_url is required for sink = redis".into());
                }
            }
            "grpc" => {
                if self.output.grpc_bind.as_deref().unwrap_or("").is_empty() {
                    return invalid("output.grpc_bind is required for sink = grpc".into());
                }
            }
            "stdout" => {}
            other => {
                return invalid(format!(
                    "output.sink '{other}' must be ws|stdout|redis|grpc"
                ))
            }
        }

        if self.cache.enabled && self.cache.snapshot_interval_ms == 0 {
            return invalid("cache.snapshot_interval_ms must be > 0 when cache is enabled".into());
        }

        let mut ids = std::collections::HashSet::new();
        for c in &self.chains {
            if !ids.insert(c.chain_id) {
                return invalid(format!("duplicate chain_id {}", c.chain_id));
            }
            // Only enabled chains must be fully runnable; a disabled chain may hold
            // placeholders.
            if !c.enabled {
                continue;
            }
            // The chain must be one this build knows (its predeploys + gas model are
            // baked in), and the declared gas_model must match — otherwise an unknown
            // id silently falls back to the Arbitrum L1-fee model and mis-prices.
            let spec = l2i_chains::by_id(c.chain_id).ok_or_else(|| {
                ConfigError::Invalid(format!(
                    "chain '{}' has unknown chain_id {} (not in the built-in registry)",
                    c.name, c.chain_id
                ))
            })?;
            let declared = parse_gas_model(&c.gas_model).ok_or_else(|| {
                ConfigError::Invalid(format!(
                    "chain '{}' gas_model '{}' must be op_stack|arbitrum",
                    c.name, c.gas_model
                ))
            })?;
            if declared != spec.gas_model {
                return invalid(format!(
                    "chain '{}' (id {}) declares gas_model '{}' but the registry says {:?}",
                    c.name, c.chain_id, c.gas_model, spec.gas_model
                ));
            }
            if c.http_url.trim().is_empty() {
                return invalid(format!("chain '{}' has an empty http_url", c.name));
            }
            if c.ws_url.trim().is_empty() {
                return invalid(format!("chain '{}' has an empty ws_url", c.name));
            }
            if c.reconcile_interval_ms == 0 {
                return invalid(format!(
                    "chain '{}' reconcile_interval_ms must be > 0",
                    c.name
                ));
            }
        }
        Ok(())
    }

    /// The enabled chains.
    pub fn enabled_chains(&self) -> impl Iterator<Item = &ChainConfig> {
        self.chains.iter().filter(|c| c.enabled)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_example_config() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/config.example.toml"
        );
        let cfg = Config::load(path).expect("config.example.toml loads");
        cfg.validate()
            .expect("config.example.toml is structurally valid");

        assert_eq!(cfg.schema_version, 1);
        assert_eq!(cfg.engine.transport, "http");
        assert_eq!(cfg.chains.len(), 5);
        assert_eq!(cfg.enabled_chains().count(), 5);
        // The stable infra addresses parsed as typed Addresses.
        assert_eq!(cfg.infra.multicall3, l2i_chains_multicall3());
        // Cross-chain is present.
        assert!(cfg.cross_chain.as_ref().unwrap().enabled);
    }

    fn l2i_chains_multicall3() -> Address {
        alloy_primitives::address!("cA11bde05977b3631167028862bE2a173976CA11")
    }

    fn example() -> Config {
        Config::load(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/config.example.toml"
        ))
        .unwrap()
    }

    #[test]
    fn rejects_bad_max_hops() {
        let mut cfg = example();
        cfg.engine.max_hops = 99;
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_unsupported_schema_version() {
        let mut cfg = example();
        cfg.schema_version = 2;
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_unknown_chain_id() {
        let mut cfg = example();
        cfg.chains[0].chain_id = 999_999; // not in the built-in registry
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_gas_model_mismatch() {
        let mut cfg = example();
        // Base (id 8453) is op_stack in the registry; declaring arbitrum must fail.
        let base = cfg.chains.iter_mut().find(|c| c.chain_id == 8453).unwrap();
        base.gas_model = "arbitrum".into();
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_empty_endpoint() {
        let mut cfg = example();
        cfg.chains[0].http_url = "  ".into();
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_inverted_cadence_and_zero_timeout() {
        let mut cfg = example();
        cfg.cadence.min_interval_ms = 5000;
        cfg.cadence.max_interval_ms = 1000; // min > max
        assert!(cfg.validate().is_err());

        let mut cfg = example();
        cfg.engine.call_timeout_ms = 0;
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn rejects_sink_without_its_bind() {
        let mut cfg = example();
        cfg.output.sink = "redis".into();
        cfg.output.redis_url = None; // redis sink with no url
        assert!(cfg.validate().is_err());

        let mut cfg = example();
        cfg.output.sink = "carrier_pigeon".into();
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn disabled_chain_may_hold_placeholders() {
        // A disabled chain is skipped by the runnable-chain checks.
        let mut cfg = example();
        cfg.chains[0].enabled = false;
        cfg.chains[0].http_url = "".into();
        cfg.chains[0].chain_id = 123_456; // unknown, but disabled → tolerated
        assert!(cfg.validate().is_ok());
    }

    #[test]
    fn cache_defaults_off_and_parses() {
        let cfg = example();
        assert!(!cfg.cache.enabled, "cache defaults off when [cache] absent");
        assert_eq!(cfg.cache.max_staleness_secs, 120);
    }
}
