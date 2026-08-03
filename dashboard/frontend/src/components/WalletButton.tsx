import { useState } from "react";
import { useAccount, useBalance, useConnect, useDisconnect, useSwitchChain } from "wagmi";
import { ChevronDown, LogOut, Wallet } from "lucide-react";
import { shortAddress } from "../lib/format";
import { CHAIN_ID_TO_KEY } from "../config/wagmi";
import { networkColor } from "../lib/networkMeta";

/**
 * MetaMask (and other injected/Coinbase wallet) connection control. Shows a
 * connect button when disconnected, and the address, native balance, and a
 * network switcher when connected.
 */
/** Strips wagmi/viem's verbose wrapper text down to the part worth showing. */
function walletErrorMessage(e: unknown): string {
  const msg = String((e as Error)?.message ?? e);
  if (/user rejected|denied|user disapproved/i.test(msg)) return "Rejected in wallet";
  return msg.split("\n")[0] ?? msg;
}

export function WalletButton() {
  const { address, isConnected, chain } = useAccount();
  const { connectors, connectAsync, isPending } = useConnect();
  const { disconnect } = useDisconnect();
  const { chains, switchChainAsync } = useSwitchChain();
  const { data: balance } = useBalance({ address });
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isConnected) {
    // Prefer the first-class MetaMask SDK connector; fall back to any injected
    // EIP-1193 wallet, then whatever is first.
    const preferred =
      connectors.find((c) => c.name === "MetaMask" || c.id === "metaMaskSDK") ??
      connectors.find((c) => c.type === "injected") ??
      connectors[0];
    return (
      <div className="flex flex-col items-end gap-1">
        <button
          type="button"
          onClick={async () => {
            if (!preferred) return;
            setError(null);
            try {
              await connectAsync({ connector: preferred });
            } catch (e) {
              // Previously silent: a rejected MetaMask prompt just reverted the
              // button back to "Connect Wallet" with no indication anything
              // happened.
              setError(walletErrorMessage(e));
            }
          }}
          disabled={isPending}
          className="focusable inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Wallet size={16} />
          {isPending ? "Connecting…" : "Connect Wallet"}
        </button>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
    );
  }

  const netKey = chain ? CHAIN_ID_TO_KEY[chain.id] : undefined;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="focusable inline-flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-1.5 text-sm"
      >
        <span
          className="h-2 w-2 rounded-full"
          style={{ background: netKey ? networkColor(netKey) : "var(--color-warn)" }}
        />
        <span className="tabular text-ink">{shortAddress(address)}</span>
        {balance && (
          <span className="tabular hidden text-ink-faint sm:inline">
            {Number(balance.formatted).toFixed(3)} {balance.symbol}
          </span>
        )}
        <ChevronDown size={14} className="text-ink-faint" />
      </button>

      {error && <div className="absolute right-0 mt-1 text-xs text-neg">{error}</div>}

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="glass absolute right-0 z-20 mt-2 w-60 rounded-xl border border-border p-2 shadow-2xl">
            <div className="px-2 py-1.5 text-xs text-ink-faint">Switch network</div>
            <div className="grid gap-0.5">
              {chains.map((c) => {
                const key = CHAIN_ID_TO_KEY[c.id];
                const active = chain?.id === c.id;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={async () => {
                      setError(null);
                      // Close only on success — a rejected/failed switch used to
                      // close the menu immediately, before the wallet even
                      // responded, leaving no way to tell the switch didn't happen.
                      try {
                        await switchChainAsync({ chainId: c.id });
                        setOpen(false);
                      } catch (e) {
                        setError(walletErrorMessage(e));
                      }
                    }}
                    className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-3 ${
                      active ? "text-ink" : "text-ink-muted"
                    }`}
                  >
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: key ? networkColor(key) : "var(--color-ink-faint)" }}
                    />
                    {c.name}
                    {active && <span className="ml-auto text-xs text-pos">●</span>}
                  </button>
                );
              })}
            </div>
            <div className="my-1 h-px bg-border" />
            <button
              type="button"
              onClick={() => {
                disconnect();
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink-muted hover:bg-surface-3"
            >
              <LogOut size={14} /> Disconnect
            </button>
          </div>
        </>
      )}
    </div>
  );
}
