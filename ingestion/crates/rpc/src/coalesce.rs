//! Request coalescing (single-flight).
//!
//! When several tasks ask for the same thing at the same time — e.g. the seed
//! multicall for a chain, or a reconcile read for a hot pool — we want exactly
//! **one** underlying RPC in flight, with every caller sharing its result. This is
//! the classic single-flight pattern; it cuts redundant load on the node without
//! any caching staleness (the entry lives only for the duration of the call).

use futures::future::{BoxFuture, FutureExt, Shared};
use std::collections::HashMap;
use std::future::Future;
use std::hash::Hash;
use std::sync::Mutex;

struct Entry<V> {
    gen: u64,
    fut: Shared<BoxFuture<'static, V>>,
}

/// Deduplicates concurrent calls sharing the same key.
pub struct SingleFlight<K, V> {
    inner: Mutex<Inner<K, V>>,
}

struct Inner<K, V> {
    next_gen: u64,
    inflight: HashMap<K, Entry<V>>,
}

impl<K, V> Default for SingleFlight<K, V>
where
    K: Eq + Hash + Clone,
    V: Clone + Send + 'static,
{
    fn default() -> Self {
        Self::new()
    }
}

impl<K, V> SingleFlight<K, V>
where
    K: Eq + Hash + Clone,
    V: Clone + Send + 'static,
{
    /// A new, empty single-flight group.
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner {
                next_gen: 0,
                inflight: HashMap::new(),
            }),
        }
    }

    /// Run `f` for `key`, unless an identical call is already in flight — in which
    /// case await and return that call's result. `f` is invoked at most once per
    /// coalesced group.
    pub async fn run<F, Fut>(&self, key: K, f: F) -> V
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = V> + Send + 'static,
    {
        let (created, my_gen, fut) = {
            let mut inner = self.inner.lock().unwrap();
            if let Some(e) = inner.inflight.get(&key) {
                (false, e.gen, e.fut.clone())
            } else {
                let gen = inner.next_gen;
                inner.next_gen += 1;
                let shared = f().boxed().shared();
                inner.inflight.insert(
                    key.clone(),
                    Entry {
                        gen,
                        fut: shared.clone(),
                    },
                );
                (true, gen, shared)
            }
        };

        let value = fut.await;

        // Only the creating call cleans up, and only if the entry is still its own
        // generation — so a fresh coalesced group started under the same key after
        // this one completed is never clobbered.
        if created {
            let mut inner = self.inner.lock().unwrap();
            if inner.inflight.get(&key).map(|e| e.gen) == Some(my_gen) {
                inner.inflight.remove(&key);
            }
        }
        value
    }

    /// Number of in-flight groups (for tests / metrics).
    pub fn inflight_len(&self) -> usize {
        self.inner.lock().unwrap().inflight.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::sync::Notify;

    #[tokio::test]
    async fn concurrent_calls_coalesce_to_one_invocation() {
        let sf: Arc<SingleFlight<u64, u64>> = Arc::new(SingleFlight::new());
        let calls = Arc::new(AtomicUsize::new(0));
        let gate = Arc::new(Notify::new());

        // Two concurrent callers for the same key; the underlying future blocks on
        // `gate` so the second caller is guaranteed to arrive while the first is
        // still in flight and share it.
        let spawn =
            |sf: Arc<SingleFlight<u64, u64>>, calls: Arc<AtomicUsize>, gate: Arc<Notify>| {
                tokio::spawn(async move {
                    sf.run(42, move || {
                        calls.fetch_add(1, Ordering::SeqCst);
                        async move {
                            gate.notified().await;
                            1234u64
                        }
                    })
                    .await
                })
            };

        let a = spawn(sf.clone(), calls.clone(), gate.clone());
        // Yield so `a` registers its inflight entry before `b` checks.
        tokio::task::yield_now().await;
        let b = spawn(sf.clone(), calls.clone(), gate.clone());
        tokio::task::yield_now().await;

        assert_eq!(
            sf.inflight_len(),
            1,
            "both callers share one inflight entry"
        );
        gate.notify_waiters();

        let (ra, rb) = (a.await.unwrap(), b.await.unwrap());
        assert_eq!(ra, 1234);
        assert_eq!(rb, 1234);
        assert_eq!(
            calls.load(Ordering::SeqCst),
            1,
            "underlying fn ran exactly once"
        );
        assert_eq!(sf.inflight_len(), 0, "entry cleaned up after completion");
    }

    #[tokio::test]
    async fn sequential_calls_each_run() {
        let sf: SingleFlight<u64, u64> = SingleFlight::new();
        let calls = Arc::new(AtomicUsize::new(0));
        for _ in 0..3 {
            let c = calls.clone();
            let v = sf
                .run(1, move || async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    7u64
                })
                .await;
            assert_eq!(v, 7);
        }
        // Not concurrent → no coalescing; ran three times.
        assert_eq!(calls.load(Ordering::SeqCst), 3);
        assert_eq!(sf.inflight_len(), 0);
    }
}
