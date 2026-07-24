# Ralph memory — durable gotchas

Append terse, high-value lessons here so future (blind) iterations don't relearn them the hard way.
Newest at the top. Keep each bullet to one or two lines. Cite a file/spec when relevant.

## Seeded knowledge (verify before relying on any address)

- **OP-Stack WETH is a predeploy at `0x4200000000000000000000000000000000000006`** on Optimism, Base,
  Ink, and Unichain. Arbitrum One's WETH is *not* at that address — it has its own WETH9. Don't assume
  a shared WETH across all five chains.
- **Gas models differ by stack.** OP-Stack fee = small L2 execution fee + an L1 data (blob) fee that
  depends on **calldata size** — so compact, byte-encoded routes save real money. Arbitrum Nitro uses
  its own ArbGas with an L1 calldata surcharge queried via the `ArbGasInfo` (`0x...6c`) precompile.
  Do sizing/breakeven math per-stack; see `docs/specs/01-chains.md` and `07-gas-and-yul.md`.
- **Cancun opcodes (EIP-1153 transient storage, MCOPY) are not uniformly available.** Gate transient
  storage behind the build profile and keep a `shanghai` fallback (`foundry.toml`). Confirm per-chain
  before using `tstore`/`tload` in shipped bytecode.
- **Flash-loan provider availability is chain-specific.** Aave V3 / Balancer V2 are on Optimism, Base,
  Arbitrum; their presence on **Ink** and **Unichain** must be verified, not assumed. Unichain is
  Uniswap-V4-first — prefer V4 flash-accounting / V3 `pool.flash` there. The provider layer must
  degrade gracefully when a provider is absent on a chain.
- **Callback security is the #1 drain vector.** Every flash-loan and flash-swap callback must assert
  both `msg.sender == the expected pool/vault` and `initiator == address(this)`. No exceptions.
- **Uniswap V4 uses the singleton `PoolManager` + `unlock` flash accounting with transient storage**,
  not per-pair pool contracts. Its adapter is structurally different from V2/V3 — see `03-dex-adapters.md`.
- **Do not trust `config/chains/*.json` entries flagged `"_verify": true` or set to the zero address.**
  They are placeholders. Verifying them (mode: research) is real work with cited sources.
