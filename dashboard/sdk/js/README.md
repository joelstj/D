# @l2/sdk — JavaScript / TypeScript client

Dependency-free client for the L2 Arbitrage GUI API. Works in Node ≥ 20 and modern
browsers (uses the global `fetch` and `WebSocket`).

```js
import { L2ArbitrageClient } from "@l2/sdk";

const client = new L2ArbitrageClient({ baseUrl: "http://localhost:8787" });

// Read + live-tune settings (takes effect on the next scan).
await client.updateSettings({ minProfitUsd: 40, loanAmountUsd: 100_000 });

// Stream real-time opportunities.
const stop = client.subscribe((msg) => {
  if (msg.type === "opportunity") console.log(msg.payload.netProfitUsd);
});
// …later: stop();
```

See [`example.mjs`](./example.mjs) for a runnable script. Full types ship in
`index.d.ts`.
