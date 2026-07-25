import { describe, it, expect } from "vitest";
import { ExecutionLatencyProbe, type ChainReader } from "../src/arbitrage/executionLatency";

/** A fake chain surface that advances a shared clock on each read, so stage
 *  durations are deterministic and no network is touched. */
function fakeReader(clock: { t: number }, opts: { blockMs?: number; gasMs?: number; viewMs?: number; fail?: string } = {}): ChainReader {
  return {
    async getBlockNumber() {
      if (opts.fail === "block") throw new Error("rpc down");
      clock.t += opts.blockMs ?? 5;
      return 21_000_000n;
    },
    async getGasPrice() {
      if (opts.fail === "gas") throw new Error("gas read failed");
      clock.t += opts.gasMs ?? 2;
      return 1_500_000_000n; // 1.5 gwei
    },
    async readContract() {
      if (opts.fail === "view") throw new Error("wrong address");
      clock.t += opts.viewMs ?? 3;
      return 9n;
    },
  };
}

describe("ExecutionLatencyProbe", () => {
  it("reports configured:false with no RPC (paper-mode default) and never errors", async () => {
    const probe = new ExecutionLatencyProbe({ rpcUrls: {} });
    const s = await probe.probeOnce();
    expect(s.configured).toBe(false);
    expect(s.healthy).toBe(false);
    expect(s.chain).toBeNull();
    expect(s.stages).toEqual([]);
    expect(s.error).toContain("no RPC configured");
  });

  it("times block + gas reads read-only, no contract view without an address", async () => {
    const clock = { t: 100 };
    const probe = new ExecutionLatencyProbe({
      rpcUrls: { arbitrum: "http://rpc.example" },
      reader: fakeReader(clock, { blockMs: 5, gasMs: 2 }),
      now: () => clock.t,
    });
    const s = await probe.probeOnce();
    expect(s.configured).toBe(true);
    expect(s.healthy).toBe(true);
    expect(s.chain).toBe("arbitrum");
    expect(s.blockNumber).toBe(21_000_000);
    expect(s.gasPriceGwei).toBe(1.5);
    expect(s.contractProbed).toBe(false);
    expect(s.stages.map((x) => x.stage)).toEqual(["rpc_block", "rpc_gas"]);
    expect(s.stages[0]!.ms).toBe(5);
    expect(s.stages[1]!.ms).toBe(2);
  });

  it("adds a read-only contract_view staticCall when an executor address is set", async () => {
    const clock = { t: 0 };
    const probe = new ExecutionLatencyProbe({
      rpcUrls: { arbitrum: "http://rpc.example" },
      executorAddress: "0x1111111111111111111111111111111111111111",
      reader: fakeReader(clock, { viewMs: 4 }),
      now: () => clock.t,
    });
    const s = await probe.probeOnce();
    expect(s.contractProbed).toBe(true);
    expect(s.stages.map((x) => x.stage)).toEqual(["rpc_block", "rpc_gas", "contract_view"]);
    expect(s.stages[2]!.ms).toBe(4);
  });

  it("surfaces RPC failure as healthy:false with the stages measured so far", async () => {
    const clock = { t: 0 };
    const probe = new ExecutionLatencyProbe({
      rpcUrls: { arbitrum: "http://rpc.example" },
      reader: fakeReader(clock, { fail: "gas" }),
      now: () => clock.t,
    });
    const s = await probe.probeOnce();
    expect(s.healthy).toBe(false);
    expect(s.error).toContain("gas read failed");
    expect(s.stages.map((x) => x.stage)).toEqual(["rpc_block"]); // block succeeded, gas failed
  });

  it("pins the chain when requested, else prefers arbitrum", async () => {
    const clock = { t: 0 };
    const pinned = new ExecutionLatencyProbe({
      rpcUrls: { base: "http://b", arbitrum: "http://a" },
      chain: "base",
      reader: fakeReader(clock),
      now: () => clock.t,
    });
    expect((await pinned.probeOnce()).chain).toBe("base");

    const preferred = new ExecutionLatencyProbe({
      rpcUrls: { optimism: "http://o", arbitrum: "http://a" },
      reader: fakeReader(clock),
      now: () => clock.t,
    });
    expect((await preferred.probeOnce()).chain).toBe("arbitrum");
  });

  it("caches within cacheMs so repeat get() does not re-probe", async () => {
    const clock = { t: 0 };
    let calls = 0;
    const reader: ChainReader = {
      async getBlockNumber() {
        calls += 1;
        clock.t += 1;
        return 1n;
      },
      async getGasPrice() {
        clock.t += 1;
        return 1n;
      },
      async readContract() {
        return 0n;
      },
    };
    const probe = new ExecutionLatencyProbe({
      rpcUrls: { arbitrum: "http://a" },
      reader,
      now: () => clock.t,
      cacheMs: 60_000,
    });
    await probe.get();
    await probe.get();
    expect(calls).toBe(1); // second get() served from cache
  });
});
