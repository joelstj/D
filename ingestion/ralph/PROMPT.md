# Ralph build prompt — fed verbatim to the agent every iteration

You are building the **L2 Data Ingestion Layer**: a Rust component that streams
live, on-chain-verified pool state from five L2s (Arbitrum One 42161, Base 8453,
Optimism 10, Unichain 130, Ink 57073) and feeds the `l2arb` Python detection
engine synchronized `DetectRequest` snapshots in single-digit milliseconds.

You have **no memory of previous iterations.** Everything you know is on disk.
Work in small, safe, fully-tested steps. Do exactly one task, prove it, record
it, commit, and stop.

## Every iteration, in order

1. **Read the rules.** Open `ralph/AGENTS.md` and follow every rule. The prime
   directives (real data only, never fake `verified`, never reward-hack tests,
   leave the build green, one task per iteration) override any impulse to move
   faster.

2. **Orient.**
   - Read `ralph/PROGRESS.md` — what's done, what's next, what's BLOCKED.
   - Identify the **first milestone in `docs/BUILD_PLAN.md` whose exit criteria
     are not all met.** That is the current milestone.
   - Read the relevant sections of `docs/ARCHITECTURE.md`,
     `docs/ENGINE_CONTRACT.md`, and (for the engine's exact JSON)
     `docs/reference/INTEGRATION.md`.
   - Look at the real code and test state (`cargo test` / file tree). Trust the
     code, not any assumption.

3. **Pick the single most important unmet task** for the current milestone.
   Smaller is better. If the milestone is large, pick the next atomic piece
   (one crate, one adapter, one test group).

4. **Implement it** following the module layout in `docs/ARCHITECTURE.md §4` and
   the mapping rules in `docs/ENGINE_CONTRACT.md`. Conform to the engine contract
   exactly; never change `docs/reference/INTEGRATION.md`.

5. **Prove it with tests** named in the milestone's exit criteria. For anything
   touching on-chain data, the proof is the equality test: **event-derived state
   == independent `eth_call` at the pinned block**, exactly. Use recorded real
   fixtures at pinned blocks (Tier A) — never mock data.

6. **Make Tier A green.** Run `cargo fmt --check`, `cargo clippy -D warnings`,
   `cargo test`. If red, fix it this iteration or, if genuinely blocked, revert
   the incomplete work, record the blocker in `PROGRESS.md`, and stop clean.
   **Never commit a red build. Never weaken a test to go green.**

7. **Update `PROGRESS.md`**: tick the finished item, set the next task, log any
   BLOCKED item with a concrete reason.

8. **Commit** with a clear message referencing the milestone
   (e.g. `M5: V3 Swap-decode ingestor + slot0 equality test @ block N`).

9. **Stop.** Do not start another task. The loop will restart you with a fresh
   context.

## When the build is finished

If — and only if — **every** milestone in `docs/BUILD_PLAN.md` has all exit
criteria met (Tier A green on HEAD, and the Tier-B live gates — benches within
budget, soak clean, e2e green — have passed), append a line starting with
`RALPH-COMPLETE` to `ralph/PROGRESS.md` summarizing the evidence (commit SHAs,
bench p99, soak duration). Otherwise, never write that sentinel.

## If you're unsure

Prefer the choice that (a) keeps data real and verifiable, (b) keeps the build
green, (c) conforms to the engine contract, (d) is the smallest safe step.
Record the uncertainty in `PROGRESS.md` so the next iteration sees it. Doing less,
correctly, beats doing more, wrongly.
