# Mode: HARDEN TESTS & GAS

Raise the floor on correctness and cost without adding features.

1. Run `forge coverage --report summary` (if available) and `forge snapshot`. Identify the weakest
   area: lowest coverage, missing fuzz/invariant tests, or the hottest gas path.
2. Do ONE of:
   - Add a fuzz test for math that only had example-based tests.
   - Add/extend an invariant test (profit invariant, no-token-loss, monotonic sizing bounds).
   - Add an adversarial unit test (fee-on-transfer token, zero liquidity, price at a tick boundary,
     max-uint amounts, reentrancy attempt).
   - Optimize one hot-path function in Yul/assembly **with** a differential test proving equivalence to
     the Solidity reference, and record the gas delta.
3. Never weaken an assertion to pass. `bash scripts/verify.sh` → GREEN, then `VERIFY_GAS=1` to refresh
   the snapshot. Journal the coverage/gas delta in `PROGRESS.md`. Commit. Stop.
