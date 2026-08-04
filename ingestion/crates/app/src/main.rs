//! `l2-ingest` — the single binary.
//!
//! Load one `config.toml`, validate it, start observability, health-gate the
//! engine, wire the output sink, and run one supervised ingestor per enabled chain
//! feeding a shared aggregator. 12-factor: one artifact, `GET /health`, Prometheus
//! `/metrics`, structured logs, graceful shutdown, `SIGHUP` config reload.

mod context;
mod crosschain;
mod ingestor;
mod pipeline;

use crosschain::build_cross_chain;
use l2i_config::Config;
use std::process::ExitCode;

const HELP: &str = "\
l2-ingest — L2 Data Ingestion Layer

USAGE:
    l2-ingest --config <path> [--check-config]

OPTIONS:
    --config <path>    Path to config.toml (default: config.toml)
    --check-config     Load + validate the config, print a summary, and exit
    --help             Show this help
";

#[tokio::main]
async fn main() -> ExitCode {
    let mut config_path = "config.toml".to_string();
    let mut check_only = false;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--config" => {
                if let Some(p) = args.next() {
                    config_path = p;
                } else {
                    eprintln!("--config requires a path");
                    return ExitCode::from(2);
                }
            }
            "--check-config" => check_only = true,
            "--help" | "-h" => {
                print!("{HELP}");
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("unknown argument: {other}\n\n{HELP}");
                return ExitCode::from(2);
            }
        }
    }

    let config = match Config::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config error: {e}");
            return ExitCode::FAILURE;
        }
    };
    if let Err(e) = config.validate() {
        eprintln!("config invalid: {e}");
        return ExitCode::FAILURE;
    }

    if check_only {
        print_summary(&config);
        return ExitCode::SUCCESS;
    }

    // Structured logging as configured (before anything else logs).
    l2i_observability::init_tracing(
        &config.observability.log_level,
        config.observability.log_format == "json",
    );

    match pipeline::run(config).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            tracing::error!(error = %e, "fatal");
            ExitCode::FAILURE
        }
    }
}

fn print_summary(cfg: &Config) {
    println!(
        "l2-ingest config OK (schema_version {})",
        cfg.schema_version
    );
    println!(
        "  engine:    {} {}",
        cfg.engine.transport, cfg.engine.http_url
    );
    println!(
        "  output:    {} {}",
        cfg.output.sink,
        cfg.output.ws_bind.as_deref().unwrap_or("-")
    );
    println!(
        "  health/metrics: {} / {}",
        cfg.observability.health_bind, cfg.observability.metrics_bind
    );
    println!("  chains ({} enabled):", cfg.enabled_chains().count());
    for c in &cfg.chains {
        println!(
            "    - {:<9} chain_id={:<6} gas={:<8} enabled={} registry={}",
            c.name, c.chain_id, c.gas_model, c.enabled, c.pool_registry
        );
    }
    print_cross_chain_summary(cfg);
}

/// The `cross_chain:` line(s) of `--check-config`'s summary. Runs the *real*
/// build+filter path (`crosschain::build_cross_chain` — the exact function the
/// live pipeline calls) instead of printing raw config counts, so "not
/// configured", "disabled", and "enabled but 0 usable after parsing+filtering"
/// are all distinguishable. Previously this printed unfiltered `[cross_chain]`
/// counts straight from the file, so a config full of placeholder addresses
/// looked identically "fully wired" to a genuinely usable one.
fn print_cross_chain_summary(cfg: &Config) {
    if cfg.cross_chain.is_none() {
        println!("  cross_chain: not configured");
        return;
    }
    let build = build_cross_chain(cfg);
    if !build.configured_enabled {
        println!(
            "  cross_chain: enabled=false (configured: assets={} bridges={} pairs={} — not built)",
            build.configured_assets, build.configured_bridges, build.configured_pairs
        );
        return;
    }
    let status = if build.cross_chain.is_some() {
        "usable"
    } else {
        "UNUSABLE (0 survived parsing+filtering — check addresses, bridges, enabled chains)"
    };
    println!("  cross_chain: enabled=true status={status}");
    println!(
        "    configured (raw):     assets={} bridges={} pairs={}",
        build.configured_assets, build.configured_bridges, build.configured_pairs
    );
    println!(
        "    usable (post-filter): assets={} bridges={} pairs={}",
        build.usable.assets_out, build.usable.bridges_out, build.usable.pairs_out
    );
}
