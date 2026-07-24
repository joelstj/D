//! The default sink: a WebSocket server that broadcasts each envelope to every
//! connected subscriber (GUI, execution engine, …).

use crate::envelope::Envelope;
use crate::sink::{OutputError, OutputSink, Result};
use async_trait::async_trait;
use futures::{SinkExt, StreamExt};
use std::net::SocketAddr;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast;
use tokio_tungstenite::tungstenite::Message;

/// A WebSocket broadcast sink.
pub struct WsServerSink {
    tx: broadcast::Sender<String>,
    local_addr: SocketAddr,
}

impl WsServerSink {
    /// Bind the server (e.g. `0.0.0.0:9001`) and start accepting subscribers.
    pub async fn bind(addr: &str) -> Result<Self> {
        let listener = TcpListener::bind(addr)
            .await
            .map_err(|e| OutputError::Io(format!("bind {addr}: {e}")))?;
        let local_addr = listener
            .local_addr()
            .map_err(|e| OutputError::Io(e.to_string()))?;
        let (tx, _rx) = broadcast::channel::<String>(1024);

        let accept_tx = tx.clone();
        tokio::spawn(async move {
            loop {
                match listener.accept().await {
                    Ok((stream, _peer)) => {
                        let rx = accept_tx.subscribe();
                        tokio::spawn(handle_conn(stream, rx));
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "ws accept failed");
                        break;
                    }
                }
            }
        });

        Ok(Self { tx, local_addr })
    }

    /// The actual bound address (useful when binding to port 0).
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    /// Number of connected subscribers.
    pub fn subscriber_count(&self) -> usize {
        self.tx.receiver_count()
    }
}

async fn handle_conn(stream: TcpStream, mut rx: broadcast::Receiver<String>) {
    let ws = match tokio_tungstenite::accept_async(stream).await {
        Ok(w) => w,
        Err(e) => {
            tracing::debug!(error = %e, "ws handshake failed");
            return;
        }
    };
    let (mut sink, mut source) = ws.split();
    loop {
        tokio::select! {
            msg = rx.recv() => match msg {
                Ok(s) => {
                    if sink.send(Message::Text(s.into())).await.is_err() {
                        break;
                    }
                }
                // Slow consumer fell behind — skip dropped messages, keep serving.
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(broadcast::error::RecvError::Closed) => break,
            },
            incoming = source.next() => match incoming {
                Some(Ok(Message::Close(_))) | None => break,
                Some(Ok(_)) => {} // ignore inbound frames (this is a push feed)
                Some(Err(_)) => break,
            },
        }
    }
}

#[async_trait]
impl OutputSink for WsServerSink {
    async fn publish(&self, env: &Envelope) -> Result<()> {
        let line = env.to_ndjson()?;
        // `send` errors only when there are no subscribers — not an error for us.
        let _ = self.tx.send(line);
        Ok(())
    }
}
