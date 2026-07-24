//! The outbound sink trait and the NDJSON-stdout sink.

use crate::envelope::Envelope;
use async_trait::async_trait;
use tokio::io::AsyncWriteExt;

/// An output error.
#[derive(Debug, thiserror::Error)]
pub enum OutputError {
    /// The envelope could not be serialized.
    #[error("serialize: {0}")]
    Serialize(#[from] serde_json::Error),
    /// An IO/transport failure.
    #[error("io: {0}")]
    Io(String),
    /// The requested sink isn't available in this build.
    #[error("sink unavailable: {0}")]
    Unavailable(String),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, OutputError>;

/// A destination the versioned [`Envelope`] is fanned out to. Consumers subscribe;
/// no coupling to our internals.
#[async_trait]
pub trait OutputSink: Send + Sync {
    /// Publish one envelope.
    async fn publish(&self, env: &Envelope) -> Result<()>;
}

/// NDJSON-to-stdout sink — one envelope per line.
#[derive(Clone, Copy, Debug, Default)]
pub struct StdoutSink;

#[async_trait]
impl OutputSink for StdoutSink {
    async fn publish(&self, env: &Envelope) -> Result<()> {
        let mut line = env.to_ndjson()?;
        line.push('\n');
        let mut out = tokio::io::stdout();
        out.write_all(line.as_bytes())
            .await
            .map_err(|e| OutputError::Io(e.to_string()))?;
        out.flush()
            .await
            .map_err(|e| OutputError::Io(e.to_string()))?;
        Ok(())
    }
}
