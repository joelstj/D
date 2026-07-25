# Project Agent Skills

Skills in this directory are auto-discovered by Claude Code when working in the
**D** repo. Each subdirectory contains a `SKILL.md` (with `name`/`description`
frontmatter) plus any supporting reference docs the skill links to.

## Vendored skills

| Skill | Purpose | Source |
|-------|---------|--------|
| [`drpc-rpc/`](drpc-rpc/SKILL.md) | Blockchain RPC access — balances, transactions, blocks, gas prices, contract reads across 100+ chains / 200+ networks via the DRPC gateway (MCP tools, direct HTTP, or x402 auto-key). | [drpcorg/drpc-agent-skills](https://github.com/drpcorg/drpc-agent-skills) @ `e31ba87` (MIT) |

### About `drpc-rpc`

Vendored verbatim from the upstream `skills/drpc-rpc/` directory (MIT-licensed —
see [`drpc-rpc/LICENSE`](drpc-rpc/LICENSE)). It is a **read/detection** aid that
fits this repo's role: the engine watches L2 DEX state and the ingestion layer
reads five L2s. The skill helps query on-chain data (`eth_getBalance`,
`eth_call`, `eth_getLogs`, gas prices, cross-chain comparisons) directly.

It changes nothing about the product's runtime paths and does not weaken any
safety invariant in `CLAUDE.md`: it holds no keys of ours, signs no arbitrage
transactions, and never touches the human-gated execution path. The optional
x402 flow only signs an off-chain message/USDC authorization to acquire a DRPC
**API key** — a user-authorized action for RPC access, not trade execution.

To refresh from upstream, re-copy `skills/drpc-rpc/*.md` and `LICENSE` from the
upstream repo and update the commit hash above.
