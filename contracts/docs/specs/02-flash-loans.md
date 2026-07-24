# Spec 02 — Flash-loan providers

A flash loan lets the executor borrow, trade, and repay **within one transaction**; if repayment
(+ fee) fails, the whole thing reverts. That revert-or-repay property is what makes uncollateralized
arbitrage safe. The executor borrows through a uniform `IFlashProvider` adapter and picks the cheapest
available source per (chain, token, amount).

## Providers we support

| Provider | Mechanism | Typical fee | Notes |
|----------|-----------|-------------|-------|
| **Aave V3** | `Pool.flashLoanSimple` / `flashLoan` → `executeOperation` callback | ~0.05% (chain-governed) | Broad token set on Optimism/Base/Arbitrum. Verify on Ink/Unichain. |
| **Balancer V2 Vault** | `Vault.flashLoan` → `receiveFlashLoan` callback | **0%** | Cheapest when the token sits in the Vault. Verify per chain. |
| **Uniswap V3 pool** | `pool.flash` → `uniswapV3FlashCallback` | pool fee tier (e.g. 0.05%/0.3%) | Any V3 pool holding the token; pay the pool's fee. |
| **Uniswap V4** | `PoolManager.unlock` flash-accounting (transient) | 0% borrow; settle deltas | V4-native; ideal on Unichain/Base. Structurally different — deltas, not transfers. |
| **DEX-native flash-swap** | UniV2/Solidly `swap(...,data)` optimistic transfer | swap fee | Borrow *inside* the first hop; no separate provider call. |

**Fee reality:** the "cheapest" provider depends on the token and amount. Balancer/UniV4 can be 0% but
require the token to be present with depth; Aave charges a small premium but has wide coverage; a
DEX-native flash-swap folds the borrow into a hop you were doing anyway. The provider-selection library
(**P3-T6**) scores `fee(amount) + gas(provider)` and picks the min.

## Availability is per-chain (do not assume)
Aave V3 and Balancer V2 are present on Optimism, Base, and Arbitrum; their presence on **Ink** and
**Unichain** must be **verified** (**P1-T4**) and may be absent. Unichain is Uniswap-V4-first — prefer
V4 flash-accounting or V3 `pool.flash` there. The adapter layer must **degrade gracefully**: if a
provider is absent on a chain, selection skips it; if *no* provider covers a token, the route is
rejected off-chain before submission.

## The uniform interface

```solidity
interface IFlashProvider {
    /// @notice Borrow `amount` of `token`, invoking the executor's callback with repayment owed.
    /// @param data opaque payload the executor uses to resume the route inside the callback.
    function flashBorrow(address token, uint256 amount, bytes calldata data) external;

    /// @notice Cost of borrowing `amount` of `token` from this provider, in `token` units.
    function flashFee(address token, uint256 amount) external view returns (uint256);
}
```

Each concrete adapter (Aave/Balancer/UniV3/UniV4/native) translates `flashBorrow` into that provider's
native call and normalizes its callback into the executor's `FlashRouter` (see below). The executor
never learns provider-specific shapes.

## Callback security — the #1 drain vector

Every provider calls *back* into the executor mid-transaction. An unvalidated callback lets an attacker
invoke it directly with forged parameters and drain approvals/balances. **Non-negotiable checks in every
flash callback:**

1. `msg.sender == the exact expected pool/vault` for this borrow (from the config registry, not from
   calldata). For UniV3 the caller must be the pool derived from the canonical factory + poolKey, so a
   fake pool can't impersonate it.
2. `initiator == address(this)` — the loan must have been initiated by the executor itself, not by an
   attacker who merely triggered a callback shape.
3. A **transient reentrancy flag** proves we are inside an in-flight borrow we started this transaction
   (`docs/specs/07`), and it is cleared on completion.
4. Repay the exact `amount + fee`; then the executor's profit invariant runs. Anything less reverts.

These are enforced centrally in `src/core/FlashRouter.sol` so no per-provider adapter can forget them.
Tests **P2-T5** include a malicious-caller mock that must revert.

## Repayment paths
- **Aave V3**: approve the Pool to pull `amount + premium` (or the pull model per version) at callback
  end; the Pool reverts if underfunded.
- **Balancer V2**: transfer `amount + 0` back to the Vault inside `receiveFlashLoan`.
- **Uniswap V3**: transfer `amount + fee` to the pool inside `uniswapV3FlashCallback`.
- **Uniswap V4**: settle the transient debt delta with `PoolManager` (`sync`/`settle`/`take`); no ERC20
  round-trip if the trade nets to a positive delta.
- **DEX-native flash-swap**: send the input token (or the output, depending on direction) to the pair to
  satisfy `k` inside the swap callback.

## Approvals hygiene
Never leave standing/unbounded approvals to a provider or pool. Use exact-amount approvals scoped to the
call, or transient approvals / Permit2 where supported, and zero them if a provider requires reset
semantics. Leftover allowance is an audit finding (`docs/specs/08`).

## Sizing interaction
The borrow amount is **not** a free parameter — it is the output of dynamic sizing
(`docs/specs/05-loan-sizing.md`), then clamped to the provider's available liquidity for that token.
Selection therefore runs *after* the pool-depth sizing produces a target notional.

## Phase-3 deliverables (see `ralph/BACKLOG.md`)
Aave V3 adapter · Balancer V2 Vault adapter · UniV3 `pool.flash` adapter · UniV4 flash-accounting
adapter · DEX-native flash-swap path · provider-selection library. Each with a fork test that borrows
and repays on Optimism/Base/Arbitrum (and Unichain for V4) where the provider exists.
