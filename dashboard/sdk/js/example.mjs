// Example: watch opportunities and auto-raise the profit threshold.
// Run the backend first (`pnpm --filter @l2/backend dev`), then:  node sdk/js/example.mjs
import { L2ArbitrageClient } from "./index.js";

const client = new L2ArbitrageClient({ baseUrl: "http://localhost:8787" });

console.log("health:", await client.health());
console.log("settings:", (await client.getSettings()).loanAmountUsd, "USD loan");

// Live-tune a parameter — takes effect on the next scan.
await client.updateSettings({ minProfitUsd: 40, networks: ["base", "arbitrum"] });

// Stream real-time opportunities.
const unsubscribe = client.subscribe((msg) => {
  if (msg.type === "opportunity") {
    const o = msg.payload;
    console.log(`[${o.network}] ${o.route.map((l) => l.dex).join("→")} net $${o.netProfitUsd.toFixed(2)}`);
  }
});

// Stop after 20s.
setTimeout(() => { unsubscribe(); process.exit(0); }, 20_000);
