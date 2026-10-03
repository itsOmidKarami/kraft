import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Health } from "../../types";

/** A server from before 2.0 serving this interface: it reports its `version`
 *  but not `installed`, which every server since 1.5.0rc14 sends. That is the window
 *  between 1.4's `kraft admin update` and the restart that finishes it: the
 *  files on disk (this page among them) are new and the process is old, so
 *  the board it answers cannot be trusted. `kraft admin doctor`'s restart row
 *  reads the same missing field (R10c-01). */
export const olderServer = (health: Health | null | undefined): boolean => !!health?.version && !("installed" in health);

/** `kraft admin update` replaced the package, but this server still runs the old one. */
export function restartPending(health: Health | null | undefined): boolean {
  return olderServer(health) || (!!health?.installed && !!health.version && health.installed !== health.version);
}

/** `1.5.0rc14` as a sort key, a pre-release below its final (as `update._rank` ranks it), or null. */
function rank(v: string): number[] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?/.exec(v);
  if (!m || (m[1] === "0" && m[2] === "0" && m[3] === "0")) return null;
  return [+m[1], +m[2], +m[3], m[4] ? ["a", "b", "rc"].indexOf(m[4]) : 3, +(m[5] ?? 0)];
}

/** An older release installed under a newer running server: a rollback, which a
 *  restart alone does not finish, since the newer one migrated the database (R10c-03). */
export function installedOlder(health: Health | null | undefined): boolean {
  const a = health?.installed ? rank(health.installed) : null;
  const b = health?.version ? rank(health.version) : null;
  if (!a || !b) return false;
  const at = a.findIndex((x, i) => x !== b[i]);
  return at >= 0 && a[at] < b[at];
}

/** A pending restart in one line, as the sidebar's footer and the phone's board say it, or null. */
export function restartWords(health: Health | null | undefined): string | null {
  if (!restartPending(health)) return null;
  if (olderServer(health)) return "a newer Kraft is installed: restart to finish the update";
  return installedOlder(health) ? `v${health?.installed} installed, older than this server: see About` : `v${health?.installed} installed: restart to finish the update`;
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
