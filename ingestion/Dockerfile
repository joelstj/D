# syntax=docker/dockerfile:1
#
# L2 Data Ingestion Layer — single static-ish binary in a slim runtime.
# One thing to deploy: GET /health, Prometheus /metrics, WS output, config.toml.

# ── Build ────────────────────────────────────────────────────────────────────
FROM rust:1.94-slim-bookworm AS builder
WORKDIR /build
# Cache dependencies first.
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY crates ./crates
COPY config ./config
COPY docs ./docs
RUN cargo build --release --bin l2-ingest

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 -s /usr/sbin/nologin l2ingest
COPY --from=builder /build/target/release/l2-ingest /usr/local/bin/l2-ingest
# Copy the example config + pool registries as a starting point.
COPY --from=builder /build/config /etc/l2-ingest/config
USER l2ingest

# WS output (9001), health (9090), Prometheus metrics (9100).
EXPOSE 9001 9090 9100

# Provide your own config.toml (endpoints filled) at /etc/l2-ingest/config.toml.
ENTRYPOINT ["l2-ingest"]
CMD ["--config", "/etc/l2-ingest/config.toml"]
