import type { Server as HttpServer } from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import { createLogger } from "../util/logger";

const log = createLogger("ws");

export interface WsMessage {
  type: string;
  payload: unknown;
  ts: number;
}

type SnapshotFn = () => Record<string, unknown>;

/**
 * Fan-out hub for real-time updates. Every engine event (new opportunity,
 * execution, stats, settings change, alert) is pushed to all connected clients,
 * which is what makes the dashboard update in live time. On connect a client
 * receives a full snapshot so it renders immediately without a REST round-trip.
 */
export class WsHub {
  private wss: WebSocketServer;
  private alive = new WeakMap<WebSocket, boolean>();
  private heartbeat: NodeJS.Timeout;

  constructor(
    server: HttpServer,
    private readonly getSnapshot: SnapshotFn,
    path = "/ws",
  ) {
    this.wss = new WebSocketServer({ server, path });

    this.wss.on("connection", (socket) => {
      this.alive.set(socket, true);
      log.info(`client connected (${this.wss.clients.size} total)`);
      this.send(socket, "snapshot", this.getSnapshot());

      socket.on("pong", () => this.alive.set(socket, true));
      socket.on("message", (raw) => this.onMessage(socket, raw.toString()));
      socket.on("close", () =>
        log.info(`client disconnected (${this.wss.clients.size} total)`),
      );
      socket.on("error", (err) => log.warn("socket error", err));
    });

    // Drop dead connections so broadcasts stay cheap.
    this.heartbeat = setInterval(() => {
      for (const socket of this.wss.clients) {
        if (this.alive.get(socket) === false) {
          socket.terminate();
          continue;
        }
        this.alive.set(socket, false);
        socket.ping();
      }
    }, 30_000);
    if (typeof this.heartbeat.unref === "function") this.heartbeat.unref();
  }

  private onMessage(socket: WebSocket, raw: string) {
    try {
      const msg = JSON.parse(raw) as { type?: string };
      if (msg.type === "ping") this.send(socket, "pong", {});
      else if (msg.type === "snapshot") this.send(socket, "snapshot", this.getSnapshot());
    } catch {
      /* ignore malformed client messages */
    }
  }

  broadcast(type: string, payload: unknown) {
    const data = JSON.stringify({ type, payload, ts: Date.now() } satisfies WsMessage);
    for (const socket of this.wss.clients) {
      if (socket.readyState === WebSocket.OPEN) socket.send(data);
    }
  }

  private send(socket: WebSocket, type: string, payload: unknown) {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type, payload, ts: Date.now() } satisfies WsMessage));
    }
  }

  clientCount(): number {
    return this.wss.clients.size;
  }

  close() {
    clearInterval(this.heartbeat);
    for (const socket of this.wss.clients) socket.terminate();
    this.wss.close();
  }
}
