//! Cadence: when to build+send a `DetectRequest`.
//!
//! Send on *meaningful change* (a tracked pool updated) with a **debounce floor**
//! (`min_interval_ms` — don't spam the engine) and a **heartbeat ceiling**
//! (`max_interval_ms` — send even if quiet, to stay fresh). Pure logic over an
//! injected clock, so it is deterministically testable.

use serde::{Deserialize, Serialize};

/// Cadence policy.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CadenceMode {
    /// Send only on change (respecting the debounce floor).
    OnChange,
    /// Send on a fixed interval (the heartbeat), ignoring change.
    Interval,
    /// Send on change past the debounce floor, or on the heartbeat ceiling.
    Hybrid,
}

/// Cadence configuration.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Cadence {
    /// How to decide.
    pub mode: CadenceMode,
    /// Debounce floor — minimum ms between sends.
    pub min_interval_ms: u64,
    /// Heartbeat ceiling — send even if quiet after this many ms.
    pub max_interval_ms: u64,
}

impl Cadence {
    /// Decide whether to send now.
    ///
    /// `last_sent_ms` = when the last request went out (`None` = never);
    /// `now_ms` = the current time; `has_change` = a tracked pool changed since the
    /// last send.
    pub fn should_send(&self, last_sent_ms: Option<u64>, now_ms: u64, has_change: bool) -> bool {
        let since = last_sent_ms.map(|t| now_ms.saturating_sub(t));
        let past_min = since.is_none_or(|s| s >= self.min_interval_ms);
        let past_max = since.is_none_or(|s| s >= self.max_interval_ms);
        match self.mode {
            CadenceMode::OnChange => has_change && past_min,
            CadenceMode::Interval => past_max,
            CadenceMode::Hybrid => (has_change && past_min) || past_max,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HYBRID: Cadence = Cadence {
        mode: CadenceMode::Hybrid,
        min_interval_ms: 25,
        max_interval_ms: 1000,
    };

    #[test]
    fn first_send_always_allowed() {
        assert!(HYBRID.should_send(None, 0, false));
        assert!(HYBRID.should_send(None, 12345, false));
    }

    #[test]
    fn debounce_floor_blocks_rapid_change() {
        // A change 10ms after the last send is below the 25ms floor → no send.
        assert!(!HYBRID.should_send(Some(1000), 1010, true));
        // A change 30ms after → past the floor → send.
        assert!(HYBRID.should_send(Some(1000), 1030, true));
    }

    #[test]
    fn heartbeat_sends_even_when_quiet() {
        // No change, but 1000ms elapsed → heartbeat sends.
        assert!(HYBRID.should_send(Some(1000), 2000, false));
        // No change, only 500ms → no send.
        assert!(!HYBRID.should_send(Some(1000), 1500, false));
    }

    #[test]
    fn on_change_ignores_heartbeat() {
        let c = Cadence {
            mode: CadenceMode::OnChange,
            min_interval_ms: 25,
            max_interval_ms: 1000,
        };
        assert!(
            !c.should_send(Some(1000), 5000, false),
            "no change → never send"
        );
        assert!(c.should_send(Some(1000), 1030, true));
    }

    #[test]
    fn interval_ignores_change() {
        let c = Cadence {
            mode: CadenceMode::Interval,
            min_interval_ms: 25,
            max_interval_ms: 1000,
        };
        assert!(
            !c.should_send(Some(1000), 1500, true),
            "change but before heartbeat"
        );
        assert!(c.should_send(Some(1000), 2000, false));
    }
}
