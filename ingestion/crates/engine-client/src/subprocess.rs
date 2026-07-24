//! Subprocess transport — `echo "$REQUEST" | python -m l2arb.api.runner`. One
//! request → one response → exit. Fine for batch/CLI; not the high-frequency hot
//! path (per-call process spawn cost), but kept behind the same trait for
//! portability.

use crate::{EngineClient, EngineError, Result};
use async_trait::async_trait;
use l2i_core::{DetectRequest, DetectResponse, EngineError as ContractError};
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

/// A subprocess-per-call [`EngineClient`].
#[derive(Clone, Debug)]
pub struct SubprocessEngineClient {
    program: String,
    args: Vec<String>,
    timeout: Duration,
}

impl SubprocessEngineClient {
    /// Build from a whitespace-separated command line like
    /// `python -m l2arb.api.runner` (no shell quoting — use [`Self::from_parts`]
    /// when an argument contains spaces).
    pub fn new(command: &str, timeout: Duration) -> Self {
        let mut parts = command.split_whitespace().map(String::from);
        let program = parts.next().unwrap_or_else(|| "python".into());
        Self {
            program,
            args: parts.collect(),
            timeout,
        }
    }

    /// Build from an explicit program and argument list (each argument passed
    /// verbatim — no splitting).
    pub fn from_parts(
        program: impl Into<String>,
        args: impl IntoIterator<Item = impl Into<String>>,
        timeout: Duration,
    ) -> Self {
        Self {
            program: program.into(),
            args: args.into_iter().map(Into::into).collect(),
            timeout,
        }
    }
}

#[async_trait]
impl EngineClient for SubprocessEngineClient {
    async fn health(&self) -> Result<bool> {
        // A subprocess engine is stateless — health is "is the program runnable?".
        // We optimistically report healthy; a failed `detect` surfaces problems.
        Ok(true)
    }

    async fn detect(&self, req: &DetectRequest) -> Result<DetectResponse> {
        let body = serde_json::to_vec(req).map_err(|e| EngineError::Decode(e.to_string()))?;

        let mut child = Command::new(&self.program)
            .args(&self.args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| EngineError::Transport(format!("spawn {}: {e}", self.program)))?;

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(&body)
                .await
                .map_err(|e| EngineError::Transport(e.to_string()))?;
            stdin
                .shutdown()
                .await
                .map_err(|e| EngineError::Transport(e.to_string()))?;
        }

        let output = tokio::time::timeout(self.timeout, child.wait_with_output())
            .await
            .map_err(|_| EngineError::Timeout)?
            .map_err(|e| EngineError::Transport(e.to_string()))?;

        let stdout = String::from_utf8_lossy(&output.stdout);
        if !output.status.success() {
            // Exit 1 → a JSON {"error","type"} on stdout (per the contract).
            if let Ok(err) = serde_json::from_str::<ContractError>(stdout.trim()) {
                return Err(EngineError::Engine(format!(
                    "{} ({})",
                    err.error, err.error_type
                )));
            }
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(EngineError::Engine(format!(
                "exit {:?}: {}",
                output.status.code(),
                stderr.trim()
            )));
        }
        serde_json::from_str::<DetectResponse>(stdout.trim())
            .map_err(|e| EngineError::Decode(format!("{e}: body={stdout}")))
    }
}
