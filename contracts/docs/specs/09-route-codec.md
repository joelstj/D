# Spec 09 — Route codec (the language-agnostic seam)

A route is a **compact, self-describing byte string**. The executor decodes it in Yul; any language can
build it from a ~100-line encoder. This byte format *is* the plug-and-play interface — freeze it, version
it, and back it with conformance vectors so every SDK is byte-identical.

Design goals: **small** (L1 calldata dominates fee — `docs/specs/07`), **unambiguous**, **cheap to
decode** in assembly, and **safe** (malformed input reverts cleanly). Big-endian (EVM-native) integers.

## v1 layout

### Header (fixed order)
| Field | Type | Bytes | Meaning |
|-------|------|------:|---------|
| `version` | uint8 | 1 | `0x01`. Decoder reverts on unknown version. |
| `flags` | uint8 | 1 | bit0 `useFlashProvider`; bit1 `flashViaFirstHop`; bit2 `tokenTableMode`; bit3 `recipientInline` |
| `hopCount` | uint8 | 1 | `1..MAX_HOPS` (MAX_HOPS = 8). |
| `flashProviderId` | uint8 | 1 | index into the chain's provider registry. Present iff bit0. |
| `profitToken` | address | 20 | borrowed & repaid token; profit is measured in it. |
| `borrowAmount` | uint128 | 16 | sized loan (`docs/specs/05`). `0` when bit1 (borrow folds into hop 1). |
| `minProfit` | uint128 | 16 | required profit in `profitToken`; executor reverts below it. |
| `deadline` | uint40 | 5 | unix seconds; revert if `block.timestamp >` it. |
| `maxImpactBps` | uint16 | 2 | aggregate price-impact guard (`docs/specs/05`). |
| `recipient` | address | 20 | profit sink. Present iff bit3; else defaults to caller. |

### Per hop (repeated `hopCount` times)
| Field | Type | Bytes | Meaning |
|-------|------|------:|---------|
| `venueId` | uint8 | 1 | index into the chain's DEX/adapter registry. |
| `pool` | address | 20 | pool/pair/PoolManager address for this hop. |
| `tokenIn` | address | 20 | (omitted in `tokenTableMode` — see below). |
| `tokenOut` | address | 20 | (omitted in `tokenTableMode`). |
| `minOut` | uint128 | 16 | per-hop slippage guard (defense in depth). |
| `extraLen` | uint8 | 1 | length of `extra`. |
| `extra` | bytes | `extraLen` | venue params: fee tier (uint24), tickSpacing, stable flag, V4 `PoolKey`/hook, LB bin ids, Curve indices. |

### Validation the decoder enforces
- `version == 0x01`, `1 ≤ hopCount ≤ MAX_HOPS`, total length matches the parsed structure exactly
  (no trailing bytes, no truncation).
- **Loop closure:** `hop[0].tokenIn == profitToken` and `hop[last].tokenOut == profitToken`, and
  `hop[i].tokenOut == hop[i+1].tokenIn` (token chain is continuous).
- `venueId` and `flashProviderId` are within the on-chain registry bounds.
- Any failure → a specific custom error (`BadRoute(reason)`), never a silent misparse.

## `tokenTableMode` (bit2) — optional compression
Since `tokenOut[i] == tokenIn[i+1]`, explicit per-hop tokens are redundant. In table mode the header
carries a small token table (`uint8 tokenCount` + `tokenCount × address`) and each hop references
`tokenIn`/`tokenOut` by **1-byte index** instead of 20 bytes. For a triangular route this removes
~100+ calldata bytes — real money on OP Stack. v1 decoders must support both modes; encoders may choose.
(Enable as part of the Yul/gas pass, **P7-T4**.)

## Reference & conformance
- A **language-neutral reference encoder/decoder** plus **JSON conformance vectors** live under
  `docs/specs/vectors/` (produced in **P8-T1**): each vector is `{ description, fields, expectedHex }`.
- Every SDK (TS/Python/Rust/Go) must reproduce every vector byte-for-byte in CI. This is how "any
  language" stays trustworthy — the vectors are the contract, not prose.
- On-chain, `RouteCodec` ships a **differential fuzz test** (**P2-T1**): `decode(encode(x)) == x` and the
  Yul decoder matches a plain-Solidity reference decoder over random routes.

## Why bytes, not ABI structs
ABI encoding pads every field to 32 bytes and adds offsets — several-fold larger calldata for the same
information, i.e. several-fold higher L1 fee on every trade. A packed byte layout decoded in Yul is the
difference between an opportunity being profitable or not at the margin. The route is the hot path; it
earns the assembly.

## Versioning
`version` is the first byte so the decoder can branch or reject immediately. New fields → new version;
old versions may be retired by bumping the on-chain accepted-version floor. Never silently change v1's
meaning — that would desync deployed SDKs.
