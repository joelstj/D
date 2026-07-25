# Client Selection / Routing Hints

Use this when you need to reproduce a client-specific issue or prefer a node implementation such as `reth`, `geth`, `erigon`, `nethermind`, `besu`, etc.

> Client selection is a routing hint/constraint. It chooses among healthy upstreams that DRPC has for that network. It does **not** pin a specific machine, and availability is network-dependent.

## When to use direct JSON-RPC instead of MCP

The MCP tools usually expose only `{ network, method, params }`. If you need client selection, make the call through DRPC's direct JSON-RPC HTTP endpoint for that request.

Supported public endpoint forms:

```text
POST https://lb.drpc.live/{network}/{API_KEY}?clients=reth
```

or:

```text
POST https://lb.drpc.org/ogrpc?network={network}&dkey={API_KEY}&clients=reth
```

Use the API key from the user, x402 setup, or existing MCP configuration. Do not hard-code real keys in examples or logs.

## Quick examples

### Force `reth`

```bash
curl -sS -X POST "https://lb.drpc.live/ethereum/$DRPC_API_KEY?clients=reth" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

Equivalent single-client form:

```bash
curl -sS -X POST "https://lb.drpc.live/ethereum/$DRPC_API_KEY?client_type=reth" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

### Force `reth` on Hyperliquid

```bash
curl -sS -X POST "https://lb.drpc.live/hyperliquid/$DRPC_API_KEY?clients=reth" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

### Compare default route vs one client type

Use the same payload and, for state-sensitive methods, prefer a fixed block number over `latest`.

```bash
RPC="https://lb.drpc.live/ethereum/$DRPC_API_KEY"
BODY='{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["0x1200000",false]}'

curl -sS -X POST "$RPC"              -H 'Content-Type: application/json' -d "$BODY"
curl -sS -X POST "$RPC?clients=reth" -H 'Content-Type: application/json' -d "$BODY"
```

## Query parameters

### `clients` compact syntax

`clients` accepts a comma-separated list:

```text
?clients=<client_type>[:<client_version>[:<version_condition>]],...
```

Examples:

```text
?clients=reth
?clients=reth,erigon
?clients=reth:1.0.0:min
?clients=geth:1.13.15:exact
?clients=erigon::exclude,reth
```

Rules:

- Multiple entries are alternatives/constraints; DRPC may route to any healthy upstream that satisfies them.
- `client_version` is optional.
- `version_condition` is optional and defaults to `any`. If you provide `client_version`, also provide `exact`, `min`, or `max` to make the version constraint explicit.
- Supported conditions: `any`, `exact`, `min`, `max`, `exclude`.

### Single-client syntax

Use these when building URLs programmatically:

```text
?client_type=reth
?client_type=reth&client_version=1.0.0&version_condition=min
```

`version_condition` values:

| Value | Meaning |
|-------|---------|
| `any` | any version of this client type |
| `exact` | exactly this version |
| `min` | version >= specified version |
| `max` | version <= specified version |
| `exclude` | exclude this client type/version |

## Error handling

If no upstream matches the selector, DRPC returns a routing error such as:

```json
{"error":{"message":"Can't route your request to suitable provider, if you specified certain providers revise the list","code":12}}
```

or an upstream-selection error mentioning `client_type` labels.

What to do:

1. Remove the selector and verify the request works on the default route.
2. Try a common client type for that network (`reth`, `geth`, `erigon`, `nethermind`, `besu`).
3. If reproducing a discrepancy, record both responses, the full URL **without the API key**, method, params, network, and block number/tag.

## Important notes for public skills

- Do not rely on private DRPC source code or internal provider names in public examples.
- Client type labels are not the same as `web3_clientVersion` strings or node version strings. Do not derive a selector from a version string unless you know the public client type label.
- Client selection is positive filtering. It is not a general “not this client” mechanism unless you use an `exclude` condition and another matching client remains available.
- If exact backend pinning is required, use public provider/routing parameters only when the user already has those identifiers from their DRPC setup or support context.
