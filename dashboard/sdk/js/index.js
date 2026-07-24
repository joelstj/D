// @l2/sdk — dependency-free client for the L2 Arbitrage GUI API.
// Works anywhere `fetch` and `WebSocket` exist (Node >= 20, modern browsers).

/**
 * @typedef {Object} L2ClientOptions
 * @property {string} [baseUrl] REST base, e.g. "http://localhost:8787"
 * @property {string} [wsUrl]   Override for the WebSocket URL (derived if omitted)
 * @property {string} [apiKey]  Optional bearer token for protected deployments
 */

export class L2ArbitrageClient {
  /** @param {L2ClientOptions} [options] */
  constructor(options = {}) {
    this.baseUrl = (options.baseUrl ?? "http://localhost:8787").replace(/\/$/, "");
    this.wsUrl = options.wsUrl ?? this.baseUrl.replace(/^http/, "ws") + "/ws";
    this.apiKey = options.apiKey;
  }

  /** @private */
  async _request(path, init = {}) {
    const headers = { "content-type": "application/json", ...(init.headers || {}) };
    if (this.apiKey) headers.authorization = `Bearer ${this.apiKey}`;
    const res = await fetch(this.baseUrl + path, { ...init, headers });
    if (!res.ok) {
      let body = "";
      try { body = JSON.stringify(await res.json()); } catch { /* ignore */ }
      throw new Error(`L2 API ${res.status} ${res.statusText} ${body}`.trim());
    }
    return res.json();
  }

  /** Liveness + runtime mode. */
  health() { return this._request("/api/health"); }

  /** Supported L2 networks and DEX venues. */
  networks() { return this._request("/api/networks"); }

  /** Current engine settings. */
  getSettings() { return this._request("/api/settings"); }

  /** Merge a partial settings update; takes effect immediately. */
  updateSettings(patch) {
    return this._request("/api/settings", { method: "PATCH", body: JSON.stringify(patch) });
  }

  /** Reset settings to defaults. */
  resetSettings() { return this._request("/api/settings/reset", { method: "POST" }); }

  /** Active arbitrage opportunities (optionally filtered by network). */
  opportunities({ limit = 100, network } = {}) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (network) q.set("network", network);
    return this._request(`/api/opportunities?${q}`);
  }

  /** Engine statistics and PnL. */
  stats() { return this._request("/api/stats"); }

  /** Execute an opportunity by id (paper or live per the current mode). */
  execute(id) { return this._request(`/api/execute/${id}`, { method: "POST" }); }

  /** Enable/disable the scanning engine. */
  setEngineEnabled(enabled) {
    return this._request("/api/engine/toggle", { method: "POST", body: JSON.stringify({ enabled }) });
  }

  /**
   * Subscribe to the real-time stream. Returns an unsubscribe function.
   * @param {(msg: {type: string, payload: any, ts: number}) => void} onMessage
   * @param {{ onOpen?: () => void, onClose?: () => void, reconnect?: boolean }} [opts]
   */
  subscribe(onMessage, opts = {}) {
    const { onOpen, onClose, reconnect = true } = opts;
    let socket = null;
    let closed = false;
    let timer = null;

    const connect = () => {
      socket = new WebSocket(this.wsUrl);
      socket.addEventListener("open", () => onOpen && onOpen());
      socket.addEventListener("message", (ev) => {
        try { onMessage(JSON.parse(ev.data)); } catch { /* ignore malformed */ }
      });
      socket.addEventListener("close", () => {
        onClose && onClose();
        if (!closed && reconnect) timer = setTimeout(connect, 1500);
      });
      socket.addEventListener("error", () => socket && socket.close());
    };
    connect();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      if (socket) socket.close();
    };
  }
}

export default L2ArbitrageClient;
