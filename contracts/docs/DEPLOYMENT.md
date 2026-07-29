# Deployment & Testing Guide

## Prerequisites

- Node.js ≥ 18, npm
- A funded deployer key on your target chain
- (optional) Foundry, for the Forge workflow

```bash
npm install
cp .env.example .env   # fill in PRIVATE_KEY and RPCs
```

## Compile

```bash
npm run compile        # solc 0.8.20, viaIR, optimizer 1e6 runs, evm=paris
```

Expected: `Compiled N Solidity files successfully` with **zero warnings**.

## Test

```bash
npm test                                            # 33 offline unit tests
FORK_RPC_URL=https://arb1.arbitrum.io/rpc \
  npm run test:fork                                 # 4 live Arbitrum-fork tests
FORK_RPC_URL=https://polygon-rpc.com \
  npm run test:fork:polygon                         # 5 live Polygon-fork tests
POLYGON_RPC_URL=https://polygon-rpc.com \
ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
  npm run test:fork:crosschain                      # 1 live dual-fork cross-chain test
```

The fork tests execute under the **Cancun** hardfork (`hardhat.config.js`
`hardfork: "cancun"`) because live contracts reached over the fork use post-Paris
opcodes — see [A note on Aave V3 on forks](#a-note-on-aave-v3-on-forks). The
per-chain suites both manufacture a price dislocation and capture it atomically
via a real flash loan, once from **Balancer V2** and once from **Aave V3**. The
cross-chain suite re-points the *same* in-process chain at a different fork
mid-test via `hardhat_reset` — Polygon for the source leg, then Arbitrum for the
destination leg — so both legs run against genuinely independent live state,
not two mocked chains; only the bridge/relayer delivery between them is
simulated (see `test/fork/CrossChainDualFork.test.js`'s header comment, and
[Limitations](../README.md#limitations) in the README).

> Some free public RPCs rate-limit or reject the bulk archival calls a mainnet
> fork makes even though a single plain JSON-RPC call to them succeeds (seen
> with `polygon-rpc.com` in a sandboxed CI-like environment; `https://
> polygon.gateway.tenderly.co` and `https://polygon-bor-rpc.publicnode.com`
> both worked). If a fork test fails with a proxy/HTTP error rather than a
> contract error or assertion, try a different RPC before assuming a code
> problem.

Foundry (same sources):

```bash
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.0.2
forge build
ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
  forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vvv
POLYGON_RPC_URL=https://polygon-rpc.com \
  forge test --match-path 'test/foundry/PolygonFork.t.sol' --fork-url "$POLYGON_RPC_URL" -vvv
# Cross-chain: creates both forks itself via vm.createFork — no single --fork-url.
POLYGON_RPC_URL=https://polygon-rpc.com ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc \
  forge test --match-path 'test/foundry/CrossChainDualFork.t.sol' -vvv
```

`foundry.toml` sets `evm_version = "cancun"` so Forge executes the fork suite
under the same spec (it clamps the solc 0.8.20 compile target to Shanghai
automatically).

> This repo is **verified end-to-end with Hardhat** in CI-equivalent runs. The
> Foundry config and tests are provided as first-class, equivalent sources; if
> you use Forge, install `forge-std` and OZ as shown above.

---

## Deploy

Both deploy scripts deploy **two** contracts per chain: `FlashLoanArbitrage`
(same-chain atomic engine) and `CrossChainArbitrageExecutor` (inventory-based
cross-chain legs — deploy it on every chain you plan to bridge between; e.g.
run the deploy step once for `arbitrum` and once for `polygon` to get the
executor pair a Polygon<->Arbitrum cross-chain flow needs). Pass
`SKIP_CROSSCHAIN=1` to deploy only `FlashLoanArbitrage`, matching this
tooling's original (pre-cross-chain-deploy) behavior.

The `FlashLoanArbitrage` constructor is:

```solidity
constructor(address aavePool, address balancerVault, address admin)
```

- Pass `address(0)` for a provider you don't use on that chain (at least one
  must be non-zero).
- `admin` receives `DEFAULT_ADMIN_ROLE`, `GUARDIAN_ROLE`, `EXECUTOR_ROLE`.

`CrossChainArbitrageExecutor`'s constructor is just `constructor(address
admin)` — no provider addresses, since it never borrows; it holds inventory.

### Hardhat

```bash
npx hardhat run scripts/deploy.js --network arbitrum
npx hardhat run scripts/deploy.js --network polygon
# networks: optimism | base | arbitrum | polygon | unichain | ink
```

The script pulls provider addresses from `config/addresses.js`, deploys both
contracts, and writes a record (both addresses) to `deployments/<network>.json`.

### Foundry

```bash
AAVE_POOL=0x794a61358D6845594F94dc1DB02A252b5b4814aD \
BALANCER_VAULT=0xBA12222222228d8Ba445958a75a0704d566BF2C8 \
ADMIN=0xYourMultisig \
forge script script/Deploy.s.sol:Deploy --rpc-url "$ARBITRUM_RPC_URL" --broadcast --verify -vvvv
# Repeat with --rpc-url "$POLYGON_RPC_URL" for the Polygon-side executor pair.
```

### Post-deploy hardening

```solidity
// Run the bot on a dedicated hot key; keep admin/guardian on a multisig.
grantRole(EXECUTOR_ROLE, botHotWallet);
grantRole(GUARDIAN_ROLE, multisig);
grantRole(DEFAULT_ADMIN_ROLE, multisig);
renounceRole(EXECUTOR_ROLE, deployer);       // if the deployer shouldn't execute
renounceRole(DEFAULT_ADMIN_ROLE, deployer);  // hand control fully to the multisig
```

### Verify on the explorer

```bash
npx hardhat verify --network arbitrum <address> <aavePool> <balancerVault> <admin>
```

---

## Per-chain provider matrix

| Chain | Chain ID | Aave V3 Pool | Balancer V2 Vault | Recommended flash source | Fork-verified |
| --- | --- | --- | --- | --- | --- |
| Optimism | 10 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either | not yet |
| Base | 8453 | ✅ `0xA238…d1c5` | ✅ `0xBA12…F2C8` | either | not yet |
| Arbitrum One | 42161 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either | ✅ `test/fork/ArbitrumFork.test.js` |
| Polygon PoS | 137 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either | ✅ `test/fork/PolygonFork.test.js` |
| Unichain | 130 | ⚠️ verify | ⚠️ verify | verify before deploy | not yet |
| Ink | 57073 | ⚠️ verify | ⚠️ verify | verify before deploy | not yet |

"Fork-verified" means a live-mainnet-fork test suite actually borrows,
swaps, and repays against that chain's real, deployed contracts (not just
that an address is recorded in `config/addresses.js`) — see
[Verified in this repo](../README.md#verified-in-this-repo). Polygon<->Arbitrum
is also the pair proven end-to-end for the cross-chain (inventory-based) model
in `test/fork/CrossChainDualFork.test.js`.

**Verify every address** against the protocol's official documentation before
mainnet use — addresses in `config/addresses.js` marked `null` are pending your
verification. If neither Aave nor Balancer is live on a chain, deploy with
whichever flash provider *is* available (the contract is provider-pluggable via
its two immutables; add an interface if you need a third provider).

Balancer V2 is **0-fee** for flash loans on most L2s (Aave charges
`FLASHLOAN_PREMIUM_TOTAL`, typically 5 bps). Query the live premium any time via
`arb.aavePremiumBps()`.

---

## A note on Aave V3 on forks — the EVM hardfork matters

The live-fork suite borrows from **both Balancer V2 and Aave V3**, and both
execute against live Arbitrum state — *provided the fork runs under the Cancun
EVM spec*. Getting this wrong looks like a "fork-tooling limitation" but is
really a hardfork mismatch. The failure mode is a bare `EvmError: NotActivated`
revert deep inside the Aave `Pool`, and the spec ladder explains it exactly:

| Fork EVM spec | Aave premium read (`FLASHLOAN_PREMIUM_TOTAL`) | Aave `flashLoanSimple` | Balancer `flashLoan` |
|---------------|:---:|:---:|:---:|
| Paris         | ❌ `NotActivated` (impl emits **PUSH0**, a Shanghai opcode) | ❌ | ❌ |
| Shanghai      | ✅ | ❌ `NotActivated` (guard uses **TSTORE/TLOAD**, Cancun) | ✅ |
| **Cancun**    | ✅ | ✅ | ✅ |

Aave V3.3's flash-loan reentrancy guard uses **EIP-1153 transient storage**, so
`flashLoanSimple` only runs under Cancun. Both `hardhat.config.js`
(`hardfork: "cancun"`) and `foundry.toml` (`evm_version = "cancun"`) pin it, so
the suites exercise the **full atomic borrow → cross-DEX route → repay → profit**
lifecycle against the *live* Aave Pool, not a mock.

This is why an earlier revision of this doc reported Aave as un-forkable: the
Foundry profile was on Paris and Hardhat on Shanghai. It was never a contract
defect or a proxy/delegatecall limitation (the premium read delegatecalls too,
and works under Shanghai) — only the wrong execution spec. The Aave callback
path (`executeOperation` → route → approve → repay) also remains covered offline
via `MockAavePool`.

---

## Fork-config gotchas (already handled here)

`hardhat.config.js` pins the local EVM to `hardfork: "shanghai"` and declares
`shanghai@0` hardfork history for each L2 chain ID. Without this, EDR's default
bleeding-edge hardfork demands blob-gas header fields the L2 blocks don't carry,
and forked contract calls fail. Pin a `FORK_BLOCK` for a faster, cached,
reproducible fork.
