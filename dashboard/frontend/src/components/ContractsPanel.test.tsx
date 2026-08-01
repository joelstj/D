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
    busy: { compiling: false, deploying: null, readiness: false },
    readiness: null,
    notice: null,
    onCompile: vi.fn(),
    onDeploy: vi.fn(),
    onRunReadiness: vi.fn(),
    ...over,
  };
}

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
            { network: "arbitrum", chainId: 42161, address: "0x1", crossChainAddress: null, configured: true, hasCode: true, premiumBps: 5, crossChainHasCode: null, healthy: true, error: null },
            { network: "base", chainId: 8453, address: "0x2", crossChainAddress: null, configured: true, hasCode: false, premiumBps: null, crossChainHasCode: null, healthy: false, error: "no code" },
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
});
