import { buildServer } from "./server";
import { createLogger } from "./util/logger";

const log = createLogger("main");

async function main() {
  const server = buildServer();
  const port = await server.start();

  log.info("─".repeat(60));
  log.info(`  L2 Arbitrage GUI backend ready`);
  log.info(`  REST:      http://localhost:${port}/api`);
  log.info(`  WebSocket: ws://localhost:${port}/ws`);
  log.info(`  Data:      ${server.env.dataSource}   Execution: ${server.env.executionMode}`);
  log.info("─".repeat(60));

  const shutdown = async (signal: string) => {
    log.info(`received ${signal}, shutting down…`);
    await server.stop();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err) => {
  log.error("fatal startup error", err);
  process.exit(1);
});
