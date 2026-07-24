//! Keep-alive HTTP client — the default hot-path transport to a long-running
//! `l2arb` FastAPI service.

use crate::{EngineClient, EngineError, Result};
use async_trait::async_trait;
use l2i_core::{DetectRequest, DetectResponse, EngineError as ContractError};
use std::time::Duration;

/// HTTP transport config.
#[derive(Clone, Debug)]
pub struct HttpConfig {
    /// Base URL, e.g. `http://127.0.0.1:8080`.
    pub base_url: String,
    /// Detect path, e.g. `/detect`.
    pub detect_path: String,
    /// Health path, e.g. `/health`.
    pub health_path: String,
    /// Per-call timeout.
    pub timeout: Duration,
}

impl Default for HttpConfig {
    fn default() -> Self {
        Self {
            base_url: "http://127.0.0.1:8080".into(),
            detect_path: "/detect".into(),
            health_path: "/health".into(),
            timeout: Duration::from_millis(50),
        }
    }
}

/// A keep-alive HTTP [`EngineClient`].
pub struct HttpEngineClient {
    client: reqwest::Client,
    detect_url: String,
    health_url: String,
    timeout: Duration,
}

impl HttpEngineClient {
    /// Build the client with a persistent connection pool.
    pub fn new(cfg: HttpConfig) -> Result<Self> {
        let client = reqwest::Client::builder()
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(8)
            .tcp_keepalive(Duration::from_secs(30))
            .build()
            .map_err(|e| EngineError::Transport(e.to_string()))?;
        let base = cfg.base_url.trim_end_matches('/');
        Ok(Self {
            client,
            detect_url: format!("{base}{}", cfg.detect_path),
            health_url: format!("{base}{}", cfg.health_path),
            timeout: cfg.timeout,
        })
    }
}

fn classify(e: reqwest::Error) -> EngineError {
    if e.is_timeout() {
        EngineError::Timeout
    } else {
        EngineError::Transport(e.to_string())
    }
}

#[async_trait]
impl EngineClient for HttpEngineClient {
    async fn health(&self) -> Result<bool> {
        let resp = self
            .client
            .get(&self.health_url)
            .timeout(self.timeout)
            .send()
            .await
            .map_err(classify)?;
        if !resp.status().is_success() {
            return Ok(false);
        }
        let v: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| EngineError::Decode(e.to_string()))?;
        Ok(v.get("status").and_then(|s| s.as_str()) == Some("ok"))
    }

    async fn detect(&self, req: &DetectRequest) -> Result<DetectResponse> {
        let resp = self
            .client
            .post(&self.detect_url)
            .timeout(self.timeout)
            .json(req)
            .send()
            .await
            .map_err(classify)?;
        let status = resp.status();
        let body = resp
            .text()
            .await
            .map_err(|e| EngineError::Decode(e.to_string()))?;
        if !status.is_success() {
            // The engine documents a JSON error shape {"error","type"}.
            if let Ok(err) = serde_json::from_str::<ContractError>(&body) {
                return Err(EngineError::Engine(format!(
                    "{} ({})",
                    err.error, err.error_type
                )));
            }
            return Err(EngineError::Engine(format!("HTTP {status}: {body}")));
        }
        serde_json::from_str::<DetectResponse>(&body)
            .map_err(|e| EngineError::Decode(format!("{e}: body={body}")))
    }
}
