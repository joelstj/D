# SDKs — integrate from any language

The dashboard has **no privileged backdoor**: everything it does is a plain
REST + WebSocket call defined in [`backend/openapi.yaml`](../backend/openapi.yaml).
That makes the engine drivable from any language or application.

| Client | Path | Transport | Dependencies |
|--------|------|-----------|--------------|
| JavaScript / TypeScript | [`sdk/js`](./js) | REST + WebSocket | none (uses global `fetch`/`WebSocket`) |
| Python | [`sdk/python`](./python) | REST (+ optional WS) | none for REST; `websocket-client` for streaming |
| Any other language | — | REST + WebSocket | use the OpenAPI spec to generate a client |

## The 30-second contract

```
GET    /api/health                 → liveness + mode
GET    /api/networks               → supported L2s + DEXes
GET    /api/settings               → current parameters
PATCH  /api/settings   {partial}   → live-tune parameters (immediate effect)
GET    /api/opportunities          → active opportunities
GET    /api/stats                  → engine stats + PnL
POST   /api/execute/:id            → execute an opportunity (paper/live per mode)
WS     /ws                         → snapshot + live opportunity/stats/execution/settings/alert stream
```

Generate a client for any language from the OpenAPI document, e.g.:

```bash
npx @openapitools/openapi-generator-cli generate \
  -i backend/openapi.yaml -g go -o ./sdk/go
```
