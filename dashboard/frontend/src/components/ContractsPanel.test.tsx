import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ContractsPanelView, type ContractsPanelViewProps } from "./ContractsPanel";
import type { ContractsStatus, NetworkContractStatus } from "../lib/types";

function net(over: Partial<NetworkContractStatus>): NetworkContractStatus {
  return {
    key: "arbitrum",
    name: "Arbitrum One",
    chainId: 42161,
    explorer: "https://arbiscan.io",
    providerVerified: true,
    aavePool: "0xaave",
    balancerVault: "0xbal",
    deployment: null,
    envWired: false,
    action: "deploy",
    ...over,
  };
}

function status(over: Partial<ContractsStatus> = {}): ContractsStatus {
  return {
    available: true,
    contractsDir: "/repo/contracts",
    compiled: true,
    artifacts: [
      { name: "FlashLoanArbitrage", role: "atomic", compiled: true, bytecodeHash: "sha256:abc", bytecodeSize: 14637 },
      { name: "CrossChainArbitrageExecutor", role: "crosschain", compiled: true, bytecodeHash: "sha256:def", bytecodeSize: 9015 },
    ],
    networks: [
      net({ key: "arbitrum", name: "Arbitrum One", action: "deploy" }),
      net({ key: "unichain", name: "Unichain", chainId: 130, providerVerified: false, aavePool: null, balancerVault: null, action: "verify-provider" }),
    ],
    generatedAt: 1,
    ...over,
  };
}

function props(over: Partial<ContractsPanelViewProps> = {}): ContractsPanelViewProps {
  return {
    status: status(),
    loading: false,
    error: null,
    wallet: { connected: false },
    busy: { compiling: false, deploying: null, readiness: false, pausing: null },
    readiness: null,
    notice: null,
    onCompile: vi.fn(),
    onDeploy: vi.fn(),
    onDeployCrossChain: vi.fn(),
    onRunReadiness: vi.fn(),
    onPauseToggle: vi.fn(),
    ...over,
  };
}

const CONNECTED_WALLET = { connected: true, address: "0xabc0000000000000000000000000000000000000" };
const ATOMIC_ADDRESS = "0x9999999999999999999999999999999999999999";

describe("ContractsPanelView", () => {
  it("shows compile status, per-chain actions, and the profit-routing guarantee", () => {
    render(<ContractsPanelView {...props()} />);
    expect(screen.getByText("Contracts")).toBeInTheDocument();
    expect(screen.getByText(/14,637 bytes/)).toBeInTheDocument();
    expect(screen.getByText("needs deploy")).toBeInTheDocument();
    expect(screen.getByText("verify provider")).toBeInTheDocument();
    expect(screen.getByText(/transferred straight to the executing wallet/i)).toBeInTheDocument();
    expect(screen.getByText(/backend never holds a key or broadcasts/i)).toBeInTheDocument();
  });

  it("disables Deploy until the wallet is connected", () => {
    const { rerender } = render(<ContractsPanelView {...props({ wallet: { connected: false } })} />);
    const deployButtons = screen.getAllByRole("button", { name: /deploy/i });
    // Arbitrum's deploy button is disabled while disconnected.
    expect(deployButtons.some((b) => (b as HTMLButtonElement).disabled)).toBe(true);

    rerender(
      <ContractsPanelView
        {...props({ wallet: { connected: true, address: "0xabc0000000000000000000000000000000000000" } })}
      />,
    );
    // Arbitrum (verified + compiled + connected) is now deployable.
    const arb = screen.getAllByRole("button", { name: /^Deploy$/ }).find((b) => !(b as HTMLButtonElement).disabled);
    expect(arb).toBeTruthy();
  });

  it("never enables Deploy for an unverified-provider chain", () => {
    render(
      <ContractsPanelView
        {...props({ wallet: { connected: true, address: "0xabc0000000000000000000000000000000000000" } })}
      />,
    );
    // Unichain row shows the block reason; its button stays disabled.
    expect(screen.getByText(/provider unverified — cannot deploy/i)).toBeInTheDocument();
  });

  it("fires onCompile and onDeploy", () => {
    const onCompile = vi.fn();
    const onDeploy = vi.fn();
    render(
      <ContractsPanelView
        {...props({
          wallet: { connected: true, address: "0xabc0000000000000000000000000000000000000" },
          onCompile,
          onDeploy,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Compile/i }));
    expect(onCompile).toHaveBeenCalledOnce();

    const arb = screen.getAllByRole("button", { name: /^Deploy$/ }).find((b) => !(b as HTMLButtonElement).disabled)!;
    fireEvent.click(arb);
    expect(onDeploy).toHaveBeenCalledOnce();
    expect(onDeploy.mock.calls[0]?.[0].key).toBe("arbitrum");
  });

  it("renders readiness sweep results", () => {
    render(
      <ContractsPanelView
        {...props({
          readiness: [
            { network: "arbitrum", chainId: 42161, address: "0x1", crossChainAddress: null, configured: true, hasCode: true, premiumBps: 5, crossChainHasCode: null, paused: false, crossChainPaused: null, healthy: true, error: null },
            { network: "base", chainId: 8453, address: "0x2", crossChainAddress: null, configured: true, hasCode: false, premiumBps: null, crossChainHasCode: null, paused: null, crossChainPaused: null, healthy: false, error: "no code" },
          ],
        })}
      />,
    );
    expect(screen.getByText(/premium 5 bps/i)).toBeInTheDocument();
    expect(screen.getByText(/no code/i)).toBeInTheDocument();
  });

  it("degrades cleanly when the contracts project is absent", () => {
    render(<ContractsPanelView {...props({ status: status({ available: false }) })} />);
    expect(screen.getByText(/Contracts project not found/i)).toBeInTheDocument();
  });

  describe("CrossChainArbitrageExecutor deploy action + status rendering (D4)", () => {
    it("renders the CrossChainArbitrageExecutor bytecode status line (previously plumbed but never shown)", () => {
      render(<ContractsPanelView {...props()} />);
      expect(screen.getByText("CrossChainArbitrageExecutor bytecode")).toBeInTheDocument();
      expect(screen.getByText(/9,015 bytes/)).toBeInTheDocument();
      expect(screen.getByText(/sha256:def/)).toBeInTheDocument();
    });

    it("shows 'not compiled' for the cross-chain artifact when it isn't compiled", () => {
      render(
        <ContractsPanelView
          {...props({
            status: status({
              artifacts: [
                { name: "FlashLoanArbitrage", role: "atomic", compiled: true, bytecodeHash: "sha256:abc", bytecodeSize: 14637 },
                { name: "CrossChainArbitrageExecutor", role: "crosschain", compiled: false, bytecodeHash: null, bytecodeSize: null },
              ],
            }),
          })}
        />,
      );
      // One "not compiled" would also match the atomic line if it were uncompiled;
      // here only the cross-chain line is, so exactly one should appear.
      expect(screen.getAllByText(/not compiled/i)).toHaveLength(1);
    });

    it("disables 'Deploy cross-chain' until the atomic contract is deployed on that network, even when connected", () => {
      // Default fixture: both networks have deployment:null.
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET })} />);
      const button = screen.getAllByRole("button", { name: /deploy cross-chain/i })[0]!;
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getAllByText(/deploy FlashLoanArbitrage first/i).length).toBeGreaterThan(0);
    });

    it("disables 'Deploy cross-chain' while disconnected even if the atomic contract IS deployed", () => {
      const deployedNetworks = [
        net({
          key: "arbitrum",
          name: "Arbitrum One",
          action: "ready",
          deployment: { network: "arbitrum", chainId: 42161, address: ATOMIC_ADDRESS, crossChainAddress: null, deployedAt: "2026-01-01T00:00:00.000Z" },
        }),
      ];
      render(
        <ContractsPanelView
          {...props({ wallet: { connected: false }, status: status({ networks: deployedNetworks }) })}
        />,
      );
      const button = screen.getByRole("button", { name: /deploy cross-chain/i });
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });

    it("enables 'Deploy cross-chain' once the atomic contract is deployed AND the wallet is connected, and fires onDeployCrossChain", () => {
      const onDeployCrossChain = vi.fn();
      const deployedNetworks = [
        net({
          key: "arbitrum",
          name: "Arbitrum One",
          action: "ready",
          deployment: { network: "arbitrum", chainId: 42161, address: ATOMIC_ADDRESS, crossChainAddress: null, deployedAt: "2026-01-01T00:00:00.000Z" },
        }),
      ];
      render(
        <ContractsPanelView
          {...props({
            wallet: CONNECTED_WALLET,
            status: status({ networks: deployedNetworks }),
            onDeployCrossChain,
          })}
        />,
      );
      const button = screen.getByRole("button", { name: /deploy cross-chain/i });
      expect((button as HTMLButtonElement).disabled).toBe(false);

      fireEvent.click(button);
      expect(onDeployCrossChain).toHaveBeenCalledOnce();
      expect(onDeployCrossChain.mock.calls[0]?.[0].key).toBe("arbitrum");
    });

    it("renders the deployed cross-chain address once recorded, and offers 'Redeploy cross-chain'", () => {
      const crossChainAddr = "0x8888888888888888888888888888888888888888";
      const deployedNetworks = [
        net({
          key: "arbitrum",
          name: "Arbitrum One",
          action: "ready",
          deployment: { network: "arbitrum", chainId: 42161, address: ATOMIC_ADDRESS, crossChainAddress: crossChainAddr, deployedAt: "2026-01-01T00:00:00.000Z" },
        }),
      ];
      render(
        <ContractsPanelView
          {...props({ wallet: CONNECTED_WALLET, status: status({ networks: deployedNetworks }) })}
        />,
      );
      // Short-formatted cross-chain address is visible in the row.
      expect(screen.getByText(/0x8888…8888/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /redeploy cross-chain/i })).toBeInTheDocument();
    });

    it("renders crossChainHasCode in the readiness sweep results (previously plumbed but never shown)", () => {
      render(
        <ContractsPanelView
          {...props({
            readiness: [
              {
                network: "arbitrum",
                chainId: 42161,
                address: "0x1",
                crossChainAddress: "0x8888888888888888888888888888888888888888",
                configured: true,
                hasCode: true,
                premiumBps: 5,
                crossChainHasCode: true,
                paused: false,
                crossChainPaused: true,
                healthy: true,
                error: null,
              },
              {
                network: "base",
                chainId: 8453,
                address: "0x2",
                crossChainAddress: "0x7777777777777777777777777777777777777777",
                configured: true,
                hasCode: true,
                premiumBps: 5,
                crossChainHasCode: false,
                paused: true,
                crossChainPaused: null,
                healthy: true,
                error: null,
              },
            ],
          })}
        />,
      );
      expect(screen.getAllByText(/↳ cross-chain/i)).toHaveLength(2);
      expect(screen.getByText(/code present/i)).toBeInTheDocument();
      // "no code" appears for base's cross-chain row; scope to avoid ambiguity
      // with any other "no code" text on the page.
      const noCodeMatches = screen.getAllByText(/no code/i);
      expect(noCodeMatches.length).toBeGreaterThan(0);
    });

    it("omits the cross-chain readiness sub-row when no cross-chain address is on record", () => {
      render(
        <ContractsPanelView
          {...props({
            readiness: [
              { network: "arbitrum", chainId: 42161, address: "0x1", crossChainAddress: null, configured: true, hasCode: true, premiumBps: 5, crossChainHasCode: null, paused: false, crossChainPaused: null, healthy: true, error: null },
            ],
          })}
        />,
      );
      expect(screen.queryByText(/↳ cross-chain/i)).not.toBeInTheDocument();
    });
  });

  // Pause/Unpause kill switch (root CLAUDE.md §12 finding O2) — same
  // MetaMask-signed pattern as deploy, added once a real contract existed to
  // pause (root CLAUDE.md §14: FlashLoanArbitrage live on Base).
  describe("Pause/Unpause kill switch (O2)", () => {
    function readinessRow(over: Partial<import("../lib/types").ReadinessResult> = {}) {
      return {
        network: "arbitrum",
        chainId: 42161,
        address: "0x1111111111111111111111111111111111111111",
        crossChainAddress: null,
        configured: true,
        hasCode: true,
        premiumBps: 5,
        crossChainHasCode: null,
        paused: false,
        crossChainPaused: null,
        healthy: true,
        error: null,
        ...over,
      };
    }

    it("shows 'active' and an enabled Pause button when paused:false", () => {
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ paused: false })] })} />);
      expect(screen.getByText("active")).toBeInTheDocument();
      const pauseBtn = screen.getByRole("button", { name: /^Pause$/i });
      const unpauseBtn = screen.getByRole("button", { name: /^Unpause$/i });
      expect((pauseBtn as HTMLButtonElement).disabled).toBe(false);
      expect((unpauseBtn as HTMLButtonElement).disabled).toBe(true); // already not paused — no-op guarded
    });

    it("shows 'PAUSED' and an enabled Unpause button when paused:true", () => {
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ paused: true })] })} />);
      expect(screen.getByText("PAUSED")).toBeInTheDocument();
      const pauseBtn = screen.getByRole("button", { name: /^Pause$/i });
      const unpauseBtn = screen.getByRole("button", { name: /^Unpause$/i });
      expect((pauseBtn as HTMLButtonElement).disabled).toBe(true); // already paused — no-op guarded
      expect((unpauseBtn as HTMLButtonElement).disabled).toBe(false);
    });

    it("never guesses 'active' when the state is unknown — shows both actions available instead", () => {
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ paused: null })] })} />);
      expect(screen.getByText(/pause state unknown/i)).toBeInTheDocument();
      expect(screen.queryByText("active")).not.toBeInTheDocument();
      expect(screen.queryByText("PAUSED")).not.toBeInTheDocument();
      const pauseBtn = screen.getByRole("button", { name: /^Pause$/i });
      const unpauseBtn = screen.getByRole("button", { name: /^Unpause$/i });
      expect((pauseBtn as HTMLButtonElement).disabled).toBe(false);
      expect((unpauseBtn as HTMLButtonElement).disabled).toBe(false);
    });

    it("disables both actions while the wallet is disconnected", () => {
      render(<ContractsPanelView {...props({ wallet: { connected: false }, readiness: [readinessRow({ paused: false })] })} />);
      expect((screen.getByRole("button", { name: /^Pause$/i }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByRole("button", { name: /^Unpause$/i }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("fires onPauseToggle(row, 'atomic', true) when Pause is clicked (active state, so Pause is the enabled action)", () => {
      const onPauseToggle = vi.fn();
      render(
        <ContractsPanelView
          {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ paused: false })], onPauseToggle })}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /^Pause$/i }));
      expect(onPauseToggle).toHaveBeenCalledOnce();
      expect(onPauseToggle).toHaveBeenCalledWith(readinessRow({ paused: false }), "atomic", true);
    });

    it("fires onPauseToggle(row, 'atomic', false) when Unpause is clicked (paused state, so Unpause is the enabled action)", () => {
      const onPauseToggle = vi.fn();
      render(
        <ContractsPanelView
          {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ paused: true })], onPauseToggle })}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /^Unpause$/i }));
      expect(onPauseToggle).toHaveBeenCalledOnce();
      expect(onPauseToggle).toHaveBeenCalledWith(readinessRow({ paused: true }), "atomic", false);
    });

    it("renders an independent kill switch for the cross-chain executor, keyed separately from the atomic one", () => {
      const onPauseToggle = vi.fn();
      const row = readinessRow({
        paused: false,
        crossChainAddress: "0x9999999999999999999999999999999999999999",
        crossChainHasCode: true,
        crossChainPaused: true,
      });
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET, readiness: [row], onPauseToggle })} />);

      // Both rows render: "active" for the atomic contract, "PAUSED" for cross-chain.
      expect(screen.getByText("active")).toBeInTheDocument();
      expect(screen.getByText("PAUSED")).toBeInTheDocument();

      const unpauseButtons = screen.getAllByRole("button", { name: /^Unpause$/i });
      expect(unpauseButtons).toHaveLength(2);
      // The cross-chain row's Unpause button is the enabled one (state is PAUSED there).
      const crossChainUnpause = unpauseButtons.find((b) => !(b as HTMLButtonElement).disabled)!;
      fireEvent.click(crossChainUnpause);
      expect(onPauseToggle).toHaveBeenCalledWith(row, "crosschain", false);
    });

    it("omits pause controls entirely when hasCode is false (nothing real to pause)", () => {
      render(
        <ContractsPanelView
          {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow({ hasCode: false, paused: null })] })}
        />,
      );
      expect(screen.queryByRole("button", { name: /^Pause$/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Unpause$/i })).not.toBeInTheDocument();
    });

    it("shows the GUARDIAN_ROLE / non-recovery safety disclosure once results are present", () => {
      render(<ContractsPanelView {...props({ wallet: CONNECTED_WALLET, readiness: [readinessRow()] })} />);
      expect(screen.getByText(/GUARDIAN_ROLE/)).toBeInTheDocument();
      expect(screen.getByText(/does not recover funds already in flight/i)).toBeInTheDocument();
    });
  });
});
