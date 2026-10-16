/** What Start Kraft runs. `admin start --detach` returns once the server answers,
 *  so the command finishes and needs no terminal. */
export const START_ARGS = ["admin", "start", "--detach"] as const;

export const ALREADY_STARTING = "Kraft is already starting.";

/** The `kraft.path` setting as a file to run: trimmed, a leading bare `~`, `~/` or `~\`
 *  taken as `home` (execFile runs no shell to do it), `kraft` on PATH when empty. */
export function startInvocation(setting: string | undefined, home: string): { file: string; args: string[] } {
  const s = (setting ?? "").trim();
  const file = !s ? "kraft" : s === "~" || s.startsWith("~/") || s.startsWith("~\\") ? home + s.slice(1) : s;
  return { file, args: [...START_ARGS] };
}

/** What a config change's restart runs in a terminal, after the binary Start
 *  Kraft runs: the terminal runs it as its process, so no shell quotes the path. */
export const RESTART_ARGS = ["admin", "restart"] as const;

/** Why the restart terminal closed on a non-zero exit, as its own message is gone with it:
 *  `kraft admin restart` on a Kraft attached to a terminal stops it and exits 1. */
export const RESTART_FAILED = (code: number) =>
  `Kraft didn't restart (exit ${code}). A Kraft that was running in a terminal stops and waits to be started again there with \`kraft\`, or here with Start Kraft.`;

/** The same exit with Kraft still answering: `kraft admin restart` also exits 1 when its
 *  running-agents question is answered no, and then nothing was stopped. */
export const RESTART_SKIPPED = (code: number) => `Kraft didn't restart (exit ${code}); it is still running.`;

/** A rejected execFile: `code` is the errno string (`ENOENT`) or the exit status. */
export type StartError = { code?: string | number | null; stderr?: string | Buffer; killed?: boolean; message?: string };

/** The line that says what went wrong: Kraft's own first `kraft: ` line (a failed
 *  detached start prints its error first, then the log tail, then the log's path),
 *  else the last non-blank line (argparse's `kraft admin start: error: …`). */
const errorLine = (s: string | Buffer | undefined) => {
  const lines = String(s ?? "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  return lines.find((l) => l.startsWith("kraft: ")) ?? lines.pop();
};

/** `kraft admin start` refused because a server holds the pidfile: Kraft is up. */
export const alreadyRunning = (err: StartError) => err.code === 1 && /\balready running\b/.test(errorLine(err.stderr) ?? "");

/** What a failed start says: the binary that was not found, else Kraft's error line
 *  from stderr (`errorLine`), else the timeout or Node's message. */
export function startFailure(err: StartError, file: string): string {
  if (err.code === "ENOENT") return `Kraft couldn't find \`${file}\`. Install it, or set kraft.path to the kraft binary.`;
  const line = errorLine(err.stderr);
  if (line) return line;
  if (err.killed) return "Kraft didn't start within 30 seconds.";
  return `Kraft didn't start: ${err.message || "unknown error"}`;
}

/** A single-flight gate: `run(fn)` calls fn and returns its promise; while an
 *  earlier run has not settled it calls `onBusy` instead and returns undefined. */
export function oneAtATime(onBusy: () => void): <T>(fn: () => PromiseLike<T>) => Promise<T> | undefined {
  let busy = false;
  return <T>(fn: () => PromiseLike<T>) => {
    if (busy) return void onBusy();
    busy = true;
    // A synchronous throw becomes a rejection, so it still frees the gate.
    return new Promise<T>((ok) => ok(fn())).finally(() => (busy = false));
  };
}

/** Where Kraft is installed from: the install page of the docs. */
export const INSTALL_URL = "https://kraft-sdlc.dev/get-started/install";

/** The buttons on a failed start's toast: a binary that is not there offers both a way to get it and a way to point at it. */
export const startButtons = (err: StartError): string[] => (err.code === "ENOENT" ? ["Get Kraft", "Open Settings"] : []);
