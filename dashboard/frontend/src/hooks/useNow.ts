import { useEffect, useState } from "react";

/** Re-renders the caller every `intervalMs` with the current epoch time, so
 * relative timestamps ("3s ago") and countdowns stay live. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
