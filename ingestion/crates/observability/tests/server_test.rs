//! M9 observability: `/health` returns ok and `/metrics` renders Prometheus text
//! including our registered metrics.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use l2i_observability::{install_metrics, names, router, LatencyTimer};
use tower::ServiceExt;

#[tokio::test]
async fn health_and_metrics_endpoints() {
    let handle = install_metrics().expect("install the Prometheus recorder once");

    // Record a couple of metrics so /metrics has content.
    metrics::counter!(names::REORGS_TOTAL).increment(1);
    {
        let _t = LatencyTimer::start(names::HOTPATH_SECONDS);
        // (work) — records on drop.
    }

    let app = router(handle);

    // /health → {"status":"ok"}.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    assert!(String::from_utf8_lossy(&body).contains("\"status\":\"ok\""));

    // /metrics → Prometheus exposition text containing our metric names.
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/metrics")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let text = String::from_utf8_lossy(&body);
    assert!(
        text.contains(names::REORGS_TOTAL),
        "missing counter:\n{text}"
    );
    assert!(
        text.contains(names::HOTPATH_SECONDS),
        "missing histogram:\n{text}"
    );
}
