//! Loads the committed example registry file through the real file loader.
use l2i_registry::{load_registry_file, PoolEntry};

#[test]
fn loads_example_arbitrum_registry() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../config/pools/arbitrum.example.toml"
    );
    let reg = load_registry_file(path).expect("example registry loads");
    assert_eq!(reg.pools.len(), 2);
    assert!(matches!(reg.pools[0], PoolEntry::V3(_)));
    assert!(matches!(reg.pools[1], PoolEntry::V2(_)));
    // Canonical token order (token0 < token1) in both entries.
    for p in &reg.pools {
        let (t0, t1) = p.tokens();
        assert!(t0 < t1, "token0 must be < token1 (canonical order)");
    }
}

#[test]
fn missing_file_errors_clearly() {
    let err = load_registry_file("/nonexistent/pools.toml").unwrap_err();
    assert!(format!("{err}").contains("pools.toml"));
}
