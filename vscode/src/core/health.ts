import type { BudgetToday } from "../../../frontend/src/types";
import { todayLine } from "./spend";
import type { ConnError } from "./store";

export const NOT_RUNNING = "Kraft isn't running — Start";
export const REFUSED_TOKEN = "Kraft refused the token — check kraft.url";
export const RECONNECTING = "Kraft: reconnecting…";
export const SIGN_IN_AGAIN = "Kraft: sign in again";
export const SIGN_IN_OFFER = "Sign in to use a session instead.";

/** The board's disconnected row: why, and the command that fixes it. With a
 *  session in use, only a 401 means it ended and the row signs in again; a 403
 *  is the server's perimeter refusing (Host/Origin), which signing in cannot fix. */
export function downRow(err: ConnError | null, signedIn = false): { label: string; command: string; args?: unknown[] } {
  if (!err || err.status === 0) return { label: NOT_RUNNING, command: "kraft.startDaemon" };
  if (signedIn && err.status === 401) return { label: SIGN_IN_AGAIN, command: "kraft.signIn" };
  if (err.status === 401 || err.status === 403) return { label: REFUSED_TOKEN, command: "workbench.action.openSettings", args: ["kraft.url"] };
  return { label: `Kraft error: ${err.detail} — Show log`, command: "kraft.showLog" };
}

export type MenuChoice = "log" | "web" | "start" | "signIn" | "retry";

/** The status bar's Quick Pick; Start only while Kraft is down, and never when
 *  signed in to another machine's Kraft: it would start one here. */
export function statusMenu(connected: boolean, opts: { remote?: boolean } = {}): { label: string; choice: MenuChoice }[] {
  return [
    { label: "Show Log", choice: "log" },
    { label: "Open Web UI", choice: "web" },
    ...(connected || opts.remote ? [] : [{ label: "Start Kraft", choice: "start" as const }]),
    { label: "Sign in to a server…", choice: "signIn" },
    { label: "Retry Now", choice: "retry" },
  ];
}

/** The status bar's text: connected, read-only, and how many items wait on you,
 *  the count left out at 0. Down shows neither: the board it counts may be stale. */
export function statusText(connected: boolean, readOnly: boolean, needs: number): string {
  if (!connected) return "$(debug-disconnect) Kraft";
  return `$(${needs > 0 ? "bell-dot" : "check"}) Kraft${readOnly ? " (read-only)" : ""}${needs > 0 ? ` ${needs}` : ""}`;
}

/** Whether the status bar takes the warning background: connected, with items waiting. */
export const statusWaiting = (connected: boolean, needs: number) => connected && needs > 0;

/** What a screen reader says for the status bar. */
export function statusLabel(connected: boolean, needs: number): string {
  if (!connected) return "Kraft: not running";
  return needs > 0 ? `Kraft: ${needs} ${needs === 1 ? "item" : "items"} waiting on you` : "Kraft: connected, nothing waiting";
}

/** The status bar's hover. Connected: the read-only reasons (`readOnly.tooltip`) and
 *  today's spend, either alone. Down: the down row's reason, since that row shows only
 *  while the board is empty; with no error yet (a socket that dropped after it opened),
 *  it is reconnecting. Spend is ignored while down: it may be stale. */
export function statusTooltip(
  connected: boolean,
  readOnly: string | undefined,
  err: ConnError | null,
  today?: BudgetToday | null,
  extra: { signedIn?: boolean; signInOffered?: boolean } = {},
): string | undefined {
  if (connected) return [readOnly, todayLine(today), extra.signInOffered ? SIGN_IN_OFFER : undefined].filter(Boolean).join("\n\n") || undefined;
  return err ? downRow(err, extra.signedIn).label : RECONNECTING;
}

/** A gate that opens at most once per `ms`. `force` always opens it and restarts the window. */
export function every(ms: number, now: () => number = Date.now): (force?: boolean) => boolean {
  let last = -Infinity;
  return (force = false) => {
    const t = now();
    if (!force && t - last < ms) return false;
    last = t;
    return true;
  };
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
