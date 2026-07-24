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
npm test                                            # 24 offline unit tests
FORK_RPC_URL=https://arb1.arbitrum.io/rpc \
  npm run test:fork                                 # 3 live Arbitrum-fork tests
```

Foundry (same sources):

```bash
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.0.2
forge build
forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vvv
```

> This repo is **verified end-to-end with Hardhat** in CI-equivalent runs. The
> Foundry config and tests are provided as first-class, equivalent sources; if
> you use Forge, install `forge-std` and OZ as shown above.

---

## Deploy

The constructor is:

```solidity
constructor(address aavePool, address balancerVault, address admin)
```

- Pass `address(0)` for a provider you don't use on that chain (at least one
  must be non-zero).
- `admin` receives `DEFAULT_ADMIN_ROLE`, `GUARDIAN_ROLE`, `EXECUTOR_ROLE`.

### Hardhat

```bash
npx hardhat run scripts/deploy.js --network arbitrum
# networks: optimism | base | arbitrum | polygon | unichain | ink
```

The script pulls provider addresses from `config/addresses.js`, deploys, and
writes a record to `deployments/<network>.json`.

### Foundry

```bash
AAVE_POOL=0x794a61358D6845594F94dc1DB02A252b5b4814aD \
BALANCER_VAULT=0xBA12222222228d8Ba445958a75a0704d566BF2C8 \
ADMIN=0xYourMultisig \
forge script script/Deploy.s.sol:Deploy --rpc-url "$ARBITRUM_RPC_URL" --broadcast --verify -vvvv
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

| Chain | Chain ID | Aave V3 Pool | Balancer V2 Vault | Recommended flash source |
| --- | --- | --- | --- | --- |
| Optimism | 10 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either |
| Base | 8453 | ✅ `0xA238…d1c5` | ✅ `0xBA12…F2C8` | either |
| Arbitrum One | 42161 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either |
| Polygon PoS | 137 | ✅ `0x794a…4aD` | ✅ `0xBA12…F2C8` | either |
| Unichain | 130 | ⚠️ verify | ⚠️ verify | verify before deploy |
| Ink | 57073 | ⚠️ verify | ⚠️ verify | verify before deploy |

**Verify every address** against the protocol's official documentation before
mainnet use — addresses in `config/addresses.js` marked `null` are pending your
verification. If neither Aave nor Balancer is live on a chain, deploy with
whichever flash provider *is* available (the contract is provider-pluggable via
its two immutables; add an interface if you need a third provider).

Balancer V2 is **0-fee** for flash loans on most L2s (Aave charges
`FLASHLOAN_PREMIUM_TOTAL`, typically 5 bps). Query the live premium any time via
`arb.aavePremiumBps()`.

---

## A note on Aave V3 on forks

The live-fork suite borrows from **Balancer**, not Aave, on purpose. Aave V3's
`Pool` is a proxy that delegatecalls to external logic libraries; that call path
**does not execute inside a Hardhat/EDR mainnet fork served by a public RPC** —
a *bare, correct* Aave flash-loan receiver reverts with empty data there too
(reproduced on both Arbitrum and Base forks). It is a **fork-tooling
limitation, not a contract defect**:

- Aave flash loans work on real chains (used by production bots daily).
- The Aave callback path (`executeOperation` → route → approve → repay) is fully
  exercised by the offline unit tests via `MockAavePool`.
- The live-fork suite still **reads the live Aave Pool** (`aavePremiumBps()`) to
  prove real integration, and uses Balancer (which forks perfectly) to prove the
  full borrow→swap→repay→profit lifecycle against live contracts.

If you specifically need Aave exercised on a fork, use an archival node and (if
still needed) `hardhat_setCode`/impersonation to substitute the Pool — or simply
rely on the mock-based coverage plus a small mainnet canary trade.

---

## Fork-config gotchas (already handled here)

`hardhat.config.js` pins the local EVM to `hardfork: "shanghai"` and declares
`shanghai@0` hardfork history for each L2 chain ID. Without this, EDR's default
bleeding-edge hardfork demands blob-gas header fields the L2 blocks don't carry,
and forked contract calls fail. Pin a `FORK_BLOCK` for a faster, cached,
reproducible fork.
