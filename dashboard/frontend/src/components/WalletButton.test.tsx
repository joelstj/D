import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WalletButton } from "./WalletButton";

// WalletButton talks to wagmi's hooks directly (no container/view split), so the
// hooks are mocked at the module level rather than restructuring the component.
const connectAsync = vi.fn();
const switchChainAsync = vi.fn();
const disconnect = vi.fn();

vi.mock("wagmi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("wagmi")>();
  return {
    ...actual,
    useAccount: () => ({ address: mockState.address, isConnected: mockState.isConnected, chain: mockState.chain }),
    useBalance: () => ({ data: undefined }),
    useConnect: () => ({
      connectors: [{ id: "metaMaskSDK", name: "MetaMask", type: "injected" }],
      connectAsync,
      isPending: false,
    }),
    useDisconnect: () => ({ disconnect }),
    useSwitchChain: () => ({
      chains: [{ id: 8453, name: "Base" }],
      switchChainAsync,
    }),
  };
});

const mockState: { address: `0x${string}` | undefined; isConnected: boolean; chain: { id: number } | undefined } = {
  address: undefined,
  isConnected: false,
  chain: undefined,
};

describe("WalletButton", () => {
  beforeEach(() => {
    connectAsync.mockReset();
    switchChainAsync.mockReset();
    disconnect.mockReset();
    mockState.address = undefined;
    mockState.isConnected = false;
    mockState.chain = undefined;
  });

  it("shows a visible error when the user rejects the MetaMask connect prompt", async () => {
    // Regression: connect() (the fire-and-forget variant) silently reverted the
    // button back to "Connect Wallet" on a rejection, with no indication a
    // prompt was ever shown or that it failed.
    connectAsync.mockRejectedValueOnce(new Error("User rejected the request"));
    render(<WalletButton />);

    fireEvent.click(screen.getByRole("button", { name: /connect wallet/i }));

    await waitFor(() => expect(connectAsync).toHaveBeenCalled());
    expect(await screen.findByText(/rejected in wallet/i)).toBeInTheDocument();
  });

  it("shows a visible error when the user rejects a network switch, and does not close the menu", async () => {
    // Regression: switchChain() (fire-and-forget) plus an unconditional
    // setOpen(false) closed the dropdown before the wallet even responded —
    // a rejected switch left no trace it had failed.
    mockState.address = "0xabc0000000000000000000000000000000abc0";
    mockState.isConnected = true;
    mockState.chain = { id: 1 };
    switchChainAsync.mockRejectedValueOnce(new Error("User rejected the request"));
    render(<WalletButton />);

    fireEvent.click(screen.getByText(/0xabc/i));
    fireEvent.click(screen.getByText("Base"));

    await waitFor(() => expect(switchChainAsync).toHaveBeenCalledWith({ chainId: 8453 }));
    expect(await screen.findByText(/rejected in wallet/i)).toBeInTheDocument();
    // The menu is still open — "Switch network" (only rendered while open) is
    // still visible, proving setOpen(false) did NOT run on the failure path.
    expect(screen.getByText("Switch network")).toBeInTheDocument();
  });

  it("closes the menu on a successful network switch", async () => {
    mockState.address = "0xabc0000000000000000000000000000000abc0";
    mockState.isConnected = true;
    mockState.chain = { id: 1 };
    switchChainAsync.mockResolvedValueOnce(undefined);
    render(<WalletButton />);

    fireEvent.click(screen.getByText(/0xabc/i));
    fireEvent.click(screen.getByText("Base"));

    await waitFor(() => expect(switchChainAsync).toHaveBeenCalledWith({ chainId: 8453 }));
    await waitFor(() => expect(screen.queryByText("Switch network")).not.toBeInTheDocument());
  });
});
