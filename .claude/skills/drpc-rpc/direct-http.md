# Direct HTTP Calls (First Session / Fallback)

When MCP tools are not yet configured, call DRPC tools directly via HTTP POST.

For client-specific routing hints (`clients=reth`, `client_type=geth`, version filters), use the direct JSON-RPC endpoint documented in [client-selection.md](client-selection.md). The MCP-over-HTTP endpoint below mirrors MCP tools and usually does not expose routing selectors.

## Endpoint

```
POST https://lb.drpc.org/mcp/{API_KEY}
```

## Headers

```
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2025-03-26
```

## Call a Tool

```bash
curl -s -X POST "https://lb.drpc.org/mcp/API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-03-26" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"TOOL_NAME","arguments":{...}}}'
```

## Response Format

Response is SSE (Server-Sent Events). Extract JSON from the `data:` line:

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"\"0x...\""}]}}
```

To parse in bash: `curl ... | grep '^data:' | sed 's/^data: //'`

The result is in `result.content[0].text`. Blockchain values are hex strings in quotes.

## Examples

### Get balance

```bash
curl -s -X POST "https://lb.drpc.org/mcp/API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-03-26" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"eth_getBalance","arguments":{"network":"ethereum","params":["0xADDRESS","latest"]}}}'
```

### List networks

```bash
curl -s -X POST "https://lb.drpc.org/mcp/API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-03-26" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_networks","arguments":{}}}'
```

### Read smart contract (ERC-20 balanceOf)

```bash
curl -s -X POST "https://lb.drpc.org/mcp/API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-03-26" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"eth_call","arguments":{"network":"ethereum","params":[{"to":"0xCONTRACT","data":"0x70a08231000000000000000000000000ADDRESS_WITHOUT_0x"},"latest"]}}}'
```

### Batch calls

```bash
curl -s -X POST "https://lb.drpc.org/mcp/API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-03-26" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"rpc_batch","arguments":{"network":"ethereum","requests":[{"method":"eth_getBalance","params":["0xADDR1","latest"]},{"method":"eth_getBalance","params":["0xADDR2","latest"]}]}}}'
```

## Notes

- API key is in the URL path, not a header
- Response is always SSE format (`text/event-stream`)
- No session state — each POST is independent
- For error codes, see [errors.md](errors.md)
