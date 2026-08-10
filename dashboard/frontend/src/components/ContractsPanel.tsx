import { useCallback, useEffect, useState } from "react";
import { useAccount, useDeployContract, useSwitchChain, useWriteContract } from "wagmi";
import { waitForTransactionReceipt } from "wagmi/actions";
import type { Abi } from "viem";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Hammer,
  Link2,
  Loader2,
  Pause,
  Play,
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
  busy: { compiling: boolean; deploying: string | null; readiness: boolean; pausing: string | null };
  readiness: ReadinessResult[] | null;
  notice: { kind: "ok" | "err"; text: string } | null;
  onCompile: () => void;
  onDeploy: (network: NetworkContractStatus) => void;
  /** Deploy `CrossChainArbitrageExecutor` on `network` (D4). */
  onDeployCrossChain: (network: NetworkContractStatus) => void;
  onRunReadiness: () => void;
  /**
   * Pause or unpause the kill switch on `target`'s executor for `r.network`
   * (root `CLAUDE.md` §12 finding O2). Same MetaMask-signed pattern as
   * deploy: the backend never signs, it only re-probes the resulting public
   * state on the next readiness sweep.
   */
  onPauseToggle: (r: ReadinessResult, target: "atomic" | "crosschain", pause: boolean) => void;
}

/** `busy.pausing` sentinel key for one network+contract's pause/unpause action. */
function pauseBusyKey(network: string, target: "atomic" | "crosschain"): string {
  return `${network}:pause:${target}`;
}

/** `busy.deploying` sentinel for the cross-chain deploy action on a network,
 *  distinct from the plain `network.key` used for the atomic deploy button. */
function crossChainBusyKey(networkKey: string): string {
  return `${networkKey}:crosschain`;
}

/**
 * Pure render of the Contracts monitor. Kept free of wagmi/react-query so it can
 * be tested with plain props (mirrors `LatencyPanelView`).
 */
export function ContractsPanelView(props: ContractsPanelViewProps) {
  const { status, wallet, busy } = props;
  const atomic = status?.artifacts.find((a) => a.role === "atomic");
  const compiled = status?.compiled ?? false;
  const crosschain = status?.artifacts.find((a) => a.role === "crosschain");
  const crossChainCompiled = crosschain?.compiled ?? false;

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
          {/* Compile status lines */}
          <div className="mb-1.5 flex items-center justify-between rounded-lg border border-border-soft bg-surface/40 px-3 py-2 text-xs">
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
          <div className="mb-3 flex items-center justify-between rounded-lg border border-border-soft bg-surface/40 px-3 py-2 text-xs">
            <span className="text-ink-muted">CrossChainArbitrageExecutor bytecode</span>
            {crossChainCompiled ? (
              <span className="tabular flex items-center gap-1.5 text-pos">
                <CheckCircle2 size={13} /> {crosschain?.bytecodeSize?.toLocaleString()} bytes ·{" "}
                {crosschain?.bytecodeHash}
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
                crossChainCompiled={crossChainCompiled}
                wallet={wallet}
                deploying={busy.deploying === n.key}
                deployingCrossChain={busy.deploying === crossChainBusyKey(n.key)}
                onDeploy={() => props.onDeploy(n)}
                onDeployCrossChain={() => props.onDeployCrossChain(n)}
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
                  <div key={r.network}>
                    <div className="flex items-center justify-between text-[11px]">
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
                    {/* Kill switch (O2): only offered once we've actually read
                        bytecode at the address, so there's a real contract to
                        pause/unpause and a real paused() value was attempted. */}
                    {r.hasCode && (
                      <PauseControl
                        paused={r.paused}
                        busy={props.busy.pausing === pauseBusyKey(r.network, "atomic")}
                        disabled={!props.wallet.connected}
                        onPause={() => props.onPauseToggle(r, "atomic", true)}
                        onUnpause={() => props.onPauseToggle(r, "atomic", false)}
                      />
                    )}
                    {/* Cross-chain executor readiness — plumbed by the backend
                        (ReadinessResult.crossChainHasCode) but previously never
                        rendered anywhere (D4). Only shown once a cross-chain
                        address is on record for this network. */}
                    {r.crossChainAddress && (
                      <div className="flex items-center justify-between pl-3 text-[11px]">
                        <span className="text-ink-faint">↳ cross-chain</span>
                        {!r.configured ? (
                          <span className="text-ink-faint">no RPC configured</span>
                        ) : r.crossChainHasCode ? (
                          <span className="tabular flex items-center gap-1 text-pos">
                            <CheckCircle2 size={11} /> code present
                          </span>
                        ) : (
                          <span className="tabular flex items-center gap-1 text-neg">
                            <AlertTriangle size={11} /> no code
                          </span>
                        )}
                      </div>
                    )}
                    {r.crossChainHasCode && (
                      <div className="pl-3">
                        <PauseControl
                          paused={r.crossChainPaused}
                          busy={props.busy.pausing === pauseBusyKey(r.network, "crosschain")}
                          disabled={!props.wallet.connected}
                          onPause={() => props.onPauseToggle(r, "crosschain", true)}
                          onUnpause={() => props.onPauseToggle(r, "crosschain", false)}
                        />
                      </div>
                    )}
                  </div>
                ))}
                <p className="pt-1 text-[10.5px] leading-relaxed text-ink-faint">
                  Pause/Unpause is signed in your wallet and requires the GUARDIAN_ROLE granted at
                  deploy time. Pausing blocks new trades — it does not recover funds already in
                  flight on a cross-chain leg (see the executor's own rescue path for that).
                </p>
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
  crossChainCompiled,
  wallet,
  deploying,
  deployingCrossChain,
  onDeploy,
  onDeployCrossChain,
}: {
  n: NetworkContractStatus;
  compiled: boolean;
  crossChainCompiled: boolean;
  wallet: { address?: string; connected: boolean };
  deploying: boolean;
  deployingCrossChain: boolean;
  onDeploy: () => void;
  onDeployCrossChain: () => void;
}) {
  const meta = ACTION_META[n.action];
  const canDeploy = compiled && n.providerVerified && wallet.connected && !deploying;
  // The cross-chain executor's constructor is `constructor(address admin)`
  // only — no flash-loan-provider dependency, so it is never gated on
  // `providerVerified`. It IS gated on the atomic contract already being
  // deployed here: recordDeployment's per-network record needs an atomic
  // `address` to attach `crossChainAddress` to (see ContractsPanel's
  // onDeployCrossChain), matching how `contracts/scripts/deploy.js` deploys
  // both together.
  const canDeployCrossChain =
    crossChainCompiled && !!n.deployment && wallet.connected && !deployingCrossChain;
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
        {/* Cross-chain executor deployment status (D4) — the same per-network
            deployment record's `crossChainAddress` field, rendered here so
            it's actually visible rather than only plumbed through the API. */}
        <div className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-faint">
          <span>cross-chain:</span>
          {n.deployment?.crossChainAddress ? (
            <a
              href={`${n.explorer}/address/${n.deployment.crossChainAddress}`}
              target="_blank"
              rel="noreferrer"
              className="tabular inline-flex items-center gap-1 hover:text-accent-2"
            >
              {shortAddress(n.deployment.crossChainAddress)} <ExternalLink size={10} />
            </a>
          ) : (
            <span>{n.deployment ? "not deployed" : "deploy FlashLoanArbitrage first"}</span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
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
          className="focusable inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
        >
          {deploying ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
          {deploying ? "Deploying…" : n.action === "ready" ? "Redeploy" : "Deploy"}
        </button>
        <button
          type="button"
          onClick={onDeployCrossChain}
          disabled={!canDeployCrossChain}
          title={
            !wallet.connected
              ? "Connect your wallet to deploy"
              : !n.deployment
                ? "Deploy FlashLoanArbitrage on this network first"
                : !crossChainCompiled
                  ? "Compile the contracts first"
                  : `Deploy CrossChainArbitrageExecutor to ${n.name} (signs in your wallet)`
          }
          className="focusable inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent-soft px-2.5 py-1 text-[11px] font-medium text-accent-2 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
        >
          {deployingCrossChain ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Link2 size={12} />
          )}
          {deployingCrossChain
            ? "Deploying…"
            : n.deployment?.crossChainAddress
              ? "Redeploy cross-chain"
              : "Deploy cross-chain"}
        </button>
      </div>
    </div>
  );
}

/**
 * Kill-switch control (root `CLAUDE.md` §12 finding O2): shown next to a
 * readiness-sweep result once bytecode is confirmed present. `paused` is the
 * freshly-probed on-chain state, never guessed — `null` means "unknown, run
 * (or re-run) the readiness sweep," and in that case both actions stay
 * available rather than blocking on missing information; once a state IS
 * known, the button that would be a same-state no-op is disabled so an
 * operator isn't tempted to fire a guaranteed-revert transaction.
 */
function PauseControl({
  paused,
  busy,
  disabled,
  onPause,
  onUnpause,
}: {
  paused: boolean | null;
  busy: boolean;
  disabled: boolean;
  onPause: () => void;
  onUnpause: () => void;
}) {
  return (
    <div className="flex items-center justify-between text-[11px]">
      <span
        className={`tabular flex items-center gap-1 ${
          paused === true ? "text-warn" : paused === false ? "text-ink-faint" : "text-ink-faint"
        }`}
      >
        {paused === true ? "PAUSED" : paused === false ? "active" : "pause state unknown"}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPause}
          disabled={disabled || busy || paused === true}
          title={disabled ? "Connect your wallet to pause" : "Pause new trades (signs in your wallet)"}
          className="focusable inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-1.5 py-0.5 text-[10.5px] text-ink-muted hover:text-warn disabled:cursor-not-allowed disabled:opacity-30"
        >
          {busy ? <Loader2 size={10} className="animate-spin" /> : <Pause size={10} />}
          Pause
        </button>
        <button
          type="button"
          onClick={onUnpause}
          disabled={disabled || busy || paused === false}
          title={disabled ? "Connect your wallet to unpause" : "Resume trades (signs in your wallet)"}
          className="focusable inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-1.5 py-0.5 text-[10.5px] text-ink-muted hover:text-pos disabled:cursor-not-allowed disabled:opacity-30"
        >
          {busy ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
          Unpause
        </button>
      </div>
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
  const { writeContractAsync } = useWriteContract();

  const [status, setStatus] = useState<ContractsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<{
    compiling: boolean;
    deploying: string | null;
    readiness: boolean;
    pausing: string | null;
  }>({
    compiling: false,
    deploying: null,
    readiness: false,
    pausing: null,
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
      setNotice({ kind: "err", text: walletErrorMessage(e) });
    } finally {
      setBusy((b) => ({ ...b, deploying: null }));
    }
  };

  /**
   * Deploy `CrossChainArbitrageExecutor` on `n` (D4). Same sanctioned
   * MetaMask-signed pattern as `onDeploy`: the backend only resolves the
   * constructor args (`deployParams(..., "CrossChainArbitrageExecutor")` —
   * just `[admin]`, no flash-loan-provider address book involved) and later
   * records the *public result*. Requires the atomic contract to already be
   * deployed on this network — `recordDeployment` attaches `crossChainAddress`
   * onto that same per-network record, so the existing atomic `address` is
   * carried forward unchanged (never overwritten with the cross-chain address).
   */
  const onDeployCrossChain = async (n: NetworkContractStatus) => {
    if (!address) {
      setNotice({ kind: "err", text: "Connect your wallet first" });
      return;
    }
    if (!n.deployment) {
      setNotice({ kind: "err", text: `Deploy FlashLoanArbitrage on ${n.name} first` });
      return;
    }
    const atomicAddress = n.deployment.address;
    setBusy((b) => ({ ...b, deploying: crossChainBusyKey(n.key) }));
    setNotice(null);
    try {
      if (chainId !== n.chainId) {
        await switchChainAsync({ chainId: n.chainId as ConfiguredChainId });
      }

      const params = await api.contracts.deployParams(n.key, address, "CrossChainArbitrageExecutor");
      const artifact = await api.contracts.artifact("CrossChainArbitrageExecutor");

      // Signed + broadcast entirely in MetaMask — the backend never sees a key.
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
        address: atomicAddress, // unchanged — this call only sets crossChainAddress
        crossChainAddress: deployed,
        deployer: address,
        txHash: hash,
        deployedAt: new Date().toISOString(),
      });
      setNotice({
        kind: "ok",
        text: `Deployed CrossChainArbitrageExecutor on ${n.name}: ${deployed} — recorded to .env`,
      });
      await refresh();
    } catch (e) {
      setNotice({ kind: "err", text: walletErrorMessage(e) });
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

  /**
   * Pause/unpause the kill switch on `r`'s atomic or cross-chain executor
   * (root `CLAUDE.md` §12 finding O2). Same sanctioned pattern as deploy: the
   * ABI is the already-compiled artifact, the call is signed entirely in
   * MetaMask, and the backend is never involved beyond serving that read-only
   * artifact — there is nothing to "record" afterward (unlike a deploy
   * address, `paused()` is live on-chain state, so we simply re-probe it via
   * the existing readiness sweep once the tx confirms).
   */
  const onPauseToggle = async (r: ReadinessResult, target: "atomic" | "crosschain", pause: boolean) => {
    const targetAddress = target === "atomic" ? r.address : r.crossChainAddress;
    if (!address) {
      setNotice({ kind: "err", text: "Connect your wallet first" });
      return;
    }
    if (!targetAddress) {
      setNotice({ kind: "err", text: "No deployed address on record for this contract" });
      return;
    }
    const contractName = target === "atomic" ? "FlashLoanArbitrage" : "CrossChainArbitrageExecutor";
    setBusy((b) => ({ ...b, pausing: pauseBusyKey(r.network, target) }));
    setNotice(null);
    try {
      if (chainId !== r.chainId) {
        await switchChainAsync({ chainId: r.chainId as ConfiguredChainId });
      }
      const artifact = await api.contracts.artifact(contractName);
      // Signed + broadcast entirely in MetaMask — same as deploy, the backend
      // never sees a key and never builds this call itself.
      const hash = await writeContractAsync({
        address: targetAddress as `0x${string}`,
        abi: artifact.abi as Abi,
        functionName: pause ? "pause" : "unpause",
      });
      await waitForTransactionReceipt(wagmiConfig, { hash });
      setNotice({ kind: "ok", text: `${pause ? "Paused" : "Unpaused"} ${contractName} on ${r.network}` });
      await onRunReadiness(); // re-probe the live paused() state, don't assume it
    } catch (e) {
      setNotice({ kind: "err", text: walletErrorMessage(e) });
    } finally {
      setBusy((b) => ({ ...b, pausing: null }));
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
      onDeployCrossChain={onDeployCrossChain}
      onRunReadiness={onRunReadiness}
      onPauseToggle={onPauseToggle}
    />
  );
}

/**
 * Turn common wallet-rejection errors into a short, human message. Shared by
 * every MetaMask-signed action this panel offers (deploy, deploy-cross-chain,
 * pause/unpause) — kept action-agnostic so a rejected *pause* doesn't
 * misreport itself as a cancelled "deployment".
 */
function walletErrorMessage(e: unknown): string {
  const msg = String((e as Error)?.message ?? e);
  if (/user rejected|denied/i.test(msg)) return "Cancelled in wallet";
  return msg;
}
