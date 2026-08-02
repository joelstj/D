import { useCallback, useEffect, useState } from "react";
import { useAccount, useDeployContract, useSwitchChain } from "wagmi";
import { waitForTransactionReceipt } from "wagmi/actions";
import type { Abi } from "viem";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Hammer,
  Loader2,
  Rocket,
  ShieldCheck,
} from "lucide-react";
import { Badge, Card } from "./ui";
import { api } from "../lib/api";
import { wagmiConfig, CHAIN_ID_TO_KEY } from "../config/wagmi";

/** The chain-id union wagmi's `switchChain` accepts (the configured chains). */
type ConfiguredChainId = (typeof wagmiConfig)["chains"][number]["id"];
import { shortAddress } from "../lib/format";
import type {
  ContractsStatus,
  ContractAction,
  NetworkContractStatus,
  ReadinessResult,
} from "../lib/types";

/* -------------------------------------------------------- presentational --- */

const ACTION_META: Record<ContractAction, { label: string; tone: "neutral" | "pos" | "neg" | "warn" | "accent" }> = {
  "verify-provider": { label: "verify provider", tone: "warn" },
  compile: { label: "needs compile", tone: "warn" },
  deploy: { label: "needs deploy", tone: "accent" },
  ready: { label: "ready", tone: "pos" },
};

export interface ContractsPanelViewProps {
  status: ContractsStatus | null;
  loading: boolean;
  error: string | null;
  wallet: { address?: string; chainKey?: string; connected: boolean };
  busy: { compiling: boolean; deploying: string | null; readiness: boolean };
  readiness: ReadinessResult[] | null;
  notice: { kind: "ok" | "err"; text: string } | null;
  onCompile: () => void;
  onDeploy: (network: NetworkContractStatus) => void;
  onRunReadiness: () => void;
}

/**
 * Pure render of the Contracts monitor. Kept free of wagmi/react-query so it can
 * be tested with plain props (mirrors `LatencyPanelView`).
 */
export function ContractsPanelView(props: ContractsPanelViewProps) {
  const { status, wallet, busy } = props;
  const atomic = status?.artifacts.find((a) => a.role === "atomic");
  const compiled = status?.compiled ?? false;

  return (
    <Card
      title="Contracts"
      subtitle="compile · deploy · monitor — deploys are signed in your wallet"
      right={
        <button
          type="button"
          onClick={props.onCompile}
          disabled={busy.compiling || !status?.available}
          className="focusable inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs font-medium text-ink-muted hover:text-ink disabled:opacity-40"
        >
          {busy.compiling ? <Loader2 size={14} className="animate-spin" /> : <Hammer size={14} />}
          {busy.compiling ? "Compiling…" : "Compile"}
        </button>
      }
    >
      {props.error && (
        <p className="mb-2 break-words text-xs text-neg">Could not load contract status: {props.error}</p>
      )}

      {!status?.available && !props.loading && !props.error && (
        <p className="text-xs text-warn">
          Contracts project not found next to the dashboard — compile/deploy is unavailable in this
          layout.
        </p>
      )}

      {status?.available && (
        <>
          {/* Compile status line */}
          <div className="mb-3 flex items-center justify-between rounded-lg border border-border-soft bg-surface/40 px-3 py-2 text-xs">
            <span className="text-ink-muted">FlashLoanArbitrage bytecode</span>
            {compiled ? (
              <span className="tabular flex items-center gap-1.5 text-pos">
                <CheckCircle2 size={13} /> {atomic?.bytecodeSize?.toLocaleString()} bytes ·{" "}
                {atomic?.bytecodeHash}
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-warn">
                <AlertTriangle size={13} /> not compiled
              </span>
            )}
          </div>

          {/* Per-network monitor */}
          <div className="space-y-1.5">
            {status.networks.map((n) => (
              <NetworkRow
                key={n.key}
                n={n}
                compiled={compiled}
                wallet={wallet}
                deploying={busy.deploying === n.key}
                onDeploy={() => props.onDeploy(n)}
              />
            ))}
          </div>

          {/* Profit-routing guarantee */}
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-border-soft bg-surface/40 px-3 py-2 text-[11px] leading-relaxed text-ink-faint">
            <ShieldCheck size={13} className="mt-0.5 shrink-0 text-pos" />
            <span>
              Profit from every successful trade is transferred straight to the executing wallet
              {wallet.address ? (
                <>
                  {" "}
                  (<span className="tabular text-ink-muted">{shortAddress(wallet.address)}</span>)
                </>
              ) : null}{" "}
              — the contract retains no funds. Deployment transactions are signed in your MetaMask;
              the backend never holds a key or broadcasts.
            </span>
          </div>

          {/* Read-only deployment stress test */}
          <div className="mt-3 border-t border-border-soft pt-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-ink-muted">Deployment stress test</span>
              <button
                type="button"
                onClick={props.onRunReadiness}
                disabled={busy.readiness}
                className="focusable inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-2.5 py-1 text-xs text-ink-muted hover:text-ink disabled:opacity-40"
              >
                {busy.readiness ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />}
                {busy.readiness ? "Probing…" : "Run readiness sweep"}
              </button>
            </div>
            {props.readiness && props.readiness.length === 0 && (
              <p className="text-[11px] text-ink-faint">
                No deployed contracts to probe yet — deploy one above first.
              </p>
            )}
            {props.readiness && props.readiness.length > 0 && (
              <div className="space-y-1">
                {props.readiness.map((r) => (
                  <div key={r.network} className="flex items-center justify-between text-[11px]">
                    <span className="text-ink-muted">{r.network}</span>
                    {!r.configured ? (
                      <span className="text-ink-faint">no RPC configured</span>
                    ) : r.healthy ? (
                      <span className="tabular flex items-center gap-1 text-pos">
                        <CheckCircle2 size={12} /> code + premium {r.premiumBps} bps
                      </span>
                    ) : (
                      <span className="tabular flex items-center gap-1 text-neg">
                        <AlertTriangle size={12} /> {r.hasCode ? "view failed" : "no code"}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {props.notice && (
            <p
              className={`mt-3 break-words text-[11px] ${
                props.notice.kind === "ok" ? "text-pos" : "text-neg"
              }`}
            >
              {props.notice.text}
            </p>
          )}
        </>
      )}
    </Card>
  );
}

function NetworkRow({
  n,
  compiled,
  wallet,
  deploying,
  onDeploy,
}: {
  n: NetworkContractStatus;
  compiled: boolean;
  wallet: { address?: string; connected: boolean };
  deploying: boolean;
  onDeploy: () => void;
}) {
  const meta = ACTION_META[n.action];
  const canDeploy = compiled && n.providerVerified && wallet.connected && !deploying;
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border-soft bg-surface/30 px-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm text-ink">{n.name}</span>
          <Badge tone={meta.tone}>{meta.label}</Badge>
          {n.envWired && <Badge tone="neutral">.env</Badge>}
        </div>
        {n.deployment ? (
          <a
            href={`${n.explorer}/address/${n.deployment.address}`}
            target="_blank"
            rel="noreferrer"
            className="tabular mt-0.5 inline-flex items-center gap-1 text-[11px] text-ink-faint hover:text-accent-2"
          >
            {shortAddress(n.deployment.address)} <ExternalLink size={11} />
          </a>
        ) : (
          <div className="mt-0.5 text-[11px] text-ink-faint">
            {n.providerVerified ? "not deployed" : "provider unverified — cannot deploy"}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onDeploy}
        disabled={!canDeploy}
        title={
          !wallet.connected
            ? "Connect your wallet to deploy"
            : !n.providerVerified
              ? "Flash-loan provider address needs verifying for this chain"
              : !compiled
                ? "Compile the contracts first"
                : `Deploy to ${n.name} (signs in your wallet)`
        }
        className="focusable inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
      >
        {deploying ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
        {deploying ? "Deploying…" : n.action === "ready" ? "Redeploy" : "Deploy"}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------- container --- */

/**
 * Wallet-connected Contracts panel. Compilation and status come from the backend;
 * the deploy transaction is built from the compiled artifact and **signed by the
 * operator's MetaMask** — the backend only records the resulting public address
 * (into `deployments/*.json` + the master `.env`). No key touches the server
 * (root `CLAUDE.md` invariant 3).
 */
export function ContractsPanel() {
  const { address, chainId, isConnected } = useAccount();
  const { switchChainAsync } = useSwitchChain();
  const { deployContractAsync } = useDeployContract();

  const [status, setStatus] = useState<ContractsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ compiling: boolean; deploying: string | null; readiness: boolean }>({
    compiling: false,
    deploying: null,
    readiness: false,
  });
  const [readiness, setReadiness] = useState<ReadinessResult[] | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.contracts.status());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Load the status snapshot once on mount.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onCompile = async () => {
    setBusy((b) => ({ ...b, compiling: true }));
    setNotice(null);
    try {
      const res = await api.contracts.compile();
      setNotice({ kind: res.ok ? "ok" : "err", text: res.ok ? "Compiled successfully" : res.output });
      await refresh();
    } catch (e) {
      setNotice({ kind: "err", text: String(e) });
    } finally {
      setBusy((b) => ({ ...b, compiling: false }));
    }
  };

  const onDeploy = async (n: NetworkContractStatus) => {
    if (!address) {
      setNotice({ kind: "err", text: "Connect your wallet first" });
      return;
    }
    setBusy((b) => ({ ...b, deploying: n.key }));
    setNotice(null);
    try {
      // Ensure MetaMask is on the target chain so the deploy lands where intended.
      // n.chainId is one of the configured chains (base/arbitrum/optimism/polygon/
      // unichain/ink) — narrow it to wagmi's chain-id union.
      if (chainId !== n.chainId) {
        await switchChainAsync({ chainId: n.chainId as ConfiguredChainId });
      }

      const params = await api.contracts.deployParams(n.key, address);
      const artifact = await api.contracts.artifact("FlashLoanArbitrage");

      // The human authorises + broadcasts this in MetaMask. The backend is not
      // involved in signing.
      const hash = await deployContractAsync({
        abi: artifact.abi as Abi,
        bytecode: artifact.bytecode,
        args: params.args,
      });
      const receipt = await waitForTransactionReceipt(wagmiConfig, { hash });
      const deployed = receipt.contractAddress;
      if (!deployed) throw new Error("deployment produced no contract address");

      await api.contracts.recordDeployment({
        network: n.key,
        chainId: n.chainId,
        address: deployed,
        deployer: address,
        txHash: hash,
        deployedAt: new Date().toISOString(),
      });
      setNotice({ kind: "ok", text: `Deployed on ${n.name}: ${deployed} — recorded to .env` });
      await refresh();
    } catch (e) {
      setNotice({ kind: "err", text: deployErrorMessage(e) });
    } finally {
      setBusy((b) => ({ ...b, deploying: null }));
    }
  };

  const onRunReadiness = async () => {
    setBusy((b) => ({ ...b, readiness: true }));
    try {
      setReadiness((await api.contracts.readiness()).results);
    } catch (e) {
      setNotice({ kind: "err", text: String(e) });
    } finally {
      setBusy((b) => ({ ...b, readiness: false }));
    }
  };

  return (
    <ContractsPanelView
      status={status}
      loading={loading}
      error={error}
      wallet={{
        address,
        chainKey: chainId ? CHAIN_ID_TO_KEY[chainId] : undefined,
        connected: isConnected,
      }}
      busy={busy}
      readiness={readiness}
      notice={notice}
      onCompile={onCompile}
      onDeploy={onDeploy}
      onRunReadiness={onRunReadiness}
    />
  );
}

/** Turn common wallet-rejection errors into a short, human message. */
function deployErrorMessage(e: unknown): string {
  const msg = String((e as Error)?.message ?? e);
  if (/user rejected|denied/i.test(msg)) return "Deployment cancelled in wallet";
  return msg;
}
