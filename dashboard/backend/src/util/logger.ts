/**
 * Tiny structured logger. No external dependency so the backend stays lean and
 * the log format is stable enough to parse from the Ralph harness.
 */
export type LogLevel = "debug" | "info" | "warn" | "error";

const LEVELS: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

const envLevel = (process.env.LOG_LEVEL as LogLevel) || "info";
const threshold = LEVELS[envLevel] ?? LEVELS.info;

function emit(level: LogLevel, scope: string, msg: string, extra?: unknown) {
  if (LEVELS[level] < threshold) return;
  const line = `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} [${scope}] ${msg}`;
  const args = extra === undefined ? [line] : [line, extra];
  if (level === "error") console.error(...args);
  else if (level === "warn") console.warn(...args);
  else console.log(...args);
}

export function createLogger(scope: string) {
  return {
    debug: (msg: string, extra?: unknown) => emit("debug", scope, msg, extra),
    info: (msg: string, extra?: unknown) => emit("info", scope, msg, extra),
    warn: (msg: string, extra?: unknown) => emit("warn", scope, msg, extra),
    error: (msg: string, extra?: unknown) => emit("error", scope, msg, extra),
  };
}

export type Logger = ReturnType<typeof createLogger>;
