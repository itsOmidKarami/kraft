import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Health } from "../../types";

/** A server from before 1.5 serving this interface: it reports its `version`
 *  but not `installed`, which every 1.5 server sends. That is the window
 *  between 1.4's `kraft admin update` and the restart that finishes it: the
 *  files on disk (this page among them) are new and the process is old, so
 *  the board it answers cannot be trusted. `kraft admin doctor`'s restart row
 *  reads the same missing field (R10c-01). */
export const olderServer = (health: Health | null | undefined): boolean => !!health?.version && !("installed" in health);

/** `kraft admin update` replaced the package, but this server still runs the old one. */
export function restartPending(health: Health | null | undefined): boolean {
  return olderServer(health) || (!!health?.installed && !!health.version && health.installed !== health.version);
}

/** The board's banner for `olderServer`: the footer line alone was easy to miss in the one window every 1.4 user goes through. */
export const OLDER_SERVER = "This server is older than its web interface: an update installed a new Kraft, and the old one is still running. What the board shows may be wrong until you restart it.";

/** `GET /health`, read on mount and polled, as the sidebar's footer always has. */
export function useHealth(pollMs = 60_000): Health | null {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    const load = () => api.getHealth().then(setHealth).catch(() => {});
    load();
    const t = setInterval(load, pollMs);
    return () => clearInterval(t);
  }, [pollMs]);
  return health;
}
