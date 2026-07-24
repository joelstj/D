//! Exponential backoff with full jitter.
//!
//! Reconnects and retries must not thundering-herd a node after an outage. We use
//! an exponential **ceiling** schedule (`initial · factor^attempt`, capped at
//! `max`) with **full jitter** — the actual delay is uniform in `[0, ceiling]`
//! (AWS's "full jitter", which minimises contention). The ceiling schedule is
//! deterministic and unit-tested; jitter is factored out as a pure function of a
//! `[0,1)` sample so it, too, is testable.

use std::time::Duration;

/// Backoff configuration.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BackoffPolicy {
    /// Delay ceiling for attempt 0.
    pub initial: Duration,
    /// Maximum delay ceiling (the schedule saturates here).
    pub max: Duration,
    /// Growth factor per attempt (typically 2.0).
    pub factor: f64,
}

impl Default for BackoffPolicy {
    fn default() -> Self {
        Self {
            initial: Duration::from_millis(250),
            max: Duration::from_secs(30),
            factor: 2.0,
        }
    }
}

impl BackoffPolicy {
    /// The un-jittered delay ceiling for a 0-based `attempt`, saturating at `max`.
    pub fn ceiling(&self, attempt: u32) -> Duration {
        let initial = self.initial.as_secs_f64();
        let max = self.max.as_secs_f64();
        // powi can overflow to +inf for large attempts; min() with max handles it,
        // and inf.min(max) == max.
        let raw = initial * self.factor.powi(attempt as i32);
        let capped = if raw.is_finite() { raw.min(max) } else { max };
        Duration::from_secs_f64(capped.max(0.0))
    }
}

/// Apply full jitter to a ceiling: `u ∈ [0,1)` maps to a delay in `[0, ceiling]`.
pub fn full_jitter(ceiling: Duration, u: f64) -> Duration {
    let u = u.clamp(0.0, 1.0);
    Duration::from_secs_f64(ceiling.as_secs_f64() * u)
}

/// A stateful backoff cursor: tracks the attempt counter and yields delays.
#[derive(Clone, Debug)]
pub struct Backoff {
    policy: BackoffPolicy,
    attempt: u32,
    rng: SplitMix64,
}

impl Backoff {
    /// Create a cursor at attempt 0, seeding jitter nondeterministically.
    pub fn new(policy: BackoffPolicy) -> Self {
        Self {
            policy,
            attempt: 0,
            rng: SplitMix64::from_entropy(),
        }
    }

    /// Create a cursor with a fixed jitter seed (deterministic — for tests).
    pub fn with_seed(policy: BackoffPolicy, seed: u64) -> Self {
        Self {
            policy,
            attempt: 0,
            rng: SplitMix64::new(seed),
        }
    }

    /// The current 0-based attempt counter.
    pub fn attempt(&self) -> u32 {
        self.attempt
    }

    /// Reset to attempt 0 (call after a successful connect).
    pub fn reset(&mut self) {
        self.attempt = 0;
    }

    /// The next un-jittered ceiling, advancing the attempt counter.
    pub fn next_ceiling(&mut self) -> Duration {
        let c = self.policy.ceiling(self.attempt);
        self.attempt = self.attempt.saturating_add(1);
        c
    }

    /// The next jittered delay, advancing the attempt counter.
    pub fn next_delay(&mut self) -> Duration {
        let ceiling = self.next_ceiling();
        full_jitter(ceiling, self.rng.next_unit())
    }
}

/// A tiny SplitMix64 PRNG — enough for jitter (no crypto need), no external dep.
#[derive(Clone, Debug)]
struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    fn from_entropy() -> Self {
        // Nanos since epoch XOR a per-process address-derived value; jitter does
        // not need unpredictability, only spread.
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x9E37_79B9_7F4A_7C15);
        let stamp = &nanos as *const u64 as u64;
        Self::new(nanos ^ stamp.rotate_left(17))
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// A sample in `[0, 1)`.
    fn next_unit(&mut self) -> f64 {
        // 53-bit mantissa → uniform [0,1).
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ceiling_schedule_doubles_then_saturates() {
        let p = BackoffPolicy {
            initial: Duration::from_millis(250),
            max: Duration::from_secs(30),
            factor: 2.0,
        };
        assert_eq!(p.ceiling(0), Duration::from_millis(250));
        assert_eq!(p.ceiling(1), Duration::from_millis(500));
        assert_eq!(p.ceiling(2), Duration::from_millis(1000));
        assert_eq!(p.ceiling(3), Duration::from_millis(2000));
        assert_eq!(p.ceiling(4), Duration::from_millis(4000));
        assert_eq!(p.ceiling(5), Duration::from_millis(8000));
        assert_eq!(p.ceiling(6), Duration::from_millis(16000));
        // 32s would exceed the 30s cap → saturates at 30s and stays there.
        assert_eq!(p.ceiling(7), Duration::from_secs(30));
        assert_eq!(p.ceiling(8), Duration::from_secs(30));
        assert_eq!(p.ceiling(100), Duration::from_secs(30));
    }

    #[test]
    fn next_ceiling_advances_attempt() {
        let mut b = Backoff::with_seed(BackoffPolicy::default(), 1);
        assert_eq!(b.attempt(), 0);
        assert_eq!(b.next_ceiling(), Duration::from_millis(250));
        assert_eq!(b.attempt(), 1);
        assert_eq!(b.next_ceiling(), Duration::from_millis(500));
        b.reset();
        assert_eq!(b.attempt(), 0);
        assert_eq!(b.next_ceiling(), Duration::from_millis(250));
    }

    #[test]
    fn full_jitter_endpoints() {
        let c = Duration::from_secs(4);
        assert_eq!(full_jitter(c, 0.0), Duration::ZERO);
        assert_eq!(full_jitter(c, 1.0), c);
        assert_eq!(full_jitter(c, 0.5), Duration::from_secs(2));
        // out-of-range clamps
        assert_eq!(full_jitter(c, -1.0), Duration::ZERO);
        assert_eq!(full_jitter(c, 2.0), c);
    }

    #[test]
    fn jittered_delay_never_exceeds_ceiling_or_max() {
        let p = BackoffPolicy::default();
        let mut b = Backoff::with_seed(p, 0xDEAD_BEEF);
        for expected_attempt in 0..50u32 {
            let ceiling = p.ceiling(expected_attempt);
            let d = b.next_delay();
            assert!(d <= ceiling, "delay {d:?} exceeded ceiling {ceiling:?}");
            assert!(d <= p.max, "delay {d:?} exceeded max {:?}", p.max);
        }
    }

    #[test]
    fn splitmix_unit_in_range() {
        let mut r = SplitMix64::new(12345);
        for _ in 0..10_000 {
            let u = r.next_unit();
            assert!((0.0..1.0).contains(&u), "unit {u} out of [0,1)");
        }
    }
}
