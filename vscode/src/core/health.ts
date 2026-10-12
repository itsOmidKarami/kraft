import type { ConnError } from "./store";

export const NOT_RUNNING = "Kraft isn't running — Start";
export const REFUSED_TOKEN = "Kraft refused the token — check kraft.url";
export const RECONNECTING = "Kraft: reconnecting…";

/** The board's disconnected row: why, and the command that fixes it. */
export function downRow(err: ConnError | null): { label: string; command: string; args?: unknown[] } {
  if (!err || err.status === 0) return { label: NOT_RUNNING, command: "kraft.startDaemon" };
  if (err.status === 401 || err.status === 403) return { label: REFUSED_TOKEN, command: "workbench.action.openSettings", args: ["kraft.url"] };
  return { label: `Kraft error: ${err.detail} — Show log`, command: "kraft.showLog" };
}

export type MenuChoice = "log" | "web" | "start" | "retry";

/** The status bar's Quick Pick; Start only while Kraft is down. */
export function statusMenu(connected: boolean): { label: string; choice: MenuChoice }[] {
  return [
    { label: "Show Log", choice: "log" },
    { label: "Open Web UI", choice: "web" },
    ...(connected ? [] : [{ label: "Start Kraft", choice: "start" as const }]),
    { label: "Retry Now", choice: "retry" },
  ];
}

/** The status bar's hover. Connected: the read-only reasons (`readOnly.tooltip`). Down:
 *  the down row's reason, since that row shows only while the board is empty; with no
 *  error yet (a socket that dropped after it opened), it is reconnecting. */
export function statusTooltip(connected: boolean, readOnly: string | undefined, err: ConnError | null): string | undefined {
  if (connected) return readOnly;
  return err ? downRow(err).label : RECONNECTING;
}

/** True the first time it sees each key, false after. */
export function onceEach(): (key: string) => boolean {
  const seen = new Set<string>();
  return (key) => !seen.has(key) && !!seen.add(key);
}

/** Whether a health reply may still apply: it answers the latest connect, and the
 *  store is still connected. A reply a drop or a newer connect overtook must not
 *  mark the status bar connected or set the version from an older server. */
export function acceptHealth(seq: number, currentSeq: number, connected: boolean): boolean {
  return seq === currentSeq && connected;
}
