import { logLineText } from "../../../frontend/src/format";
import type { LogLine, WorkerSession } from "../../../frontend/src/types";
import type { WorkItemDetail } from "./api";

export interface SseFrame { event: string; data: string }

/** The complete frames at the head of `buffer`, and what is left of a frame still arriving. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  let at = 0;
  for (let end = buffer.indexOf("\n\n"); end !== -1; end = buffer.indexOf("\n\n", at)) {
    let event = "message";
    const data: string[] = [];
    for (const raw of buffer.slice(at, end).split("\n")) {
      const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      const colon = line.indexOf(":");
      // A line that starts with a colon is a comment.
      if (colon === 0) continue;
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) frames.push({ event, data: data.join("\n") });
    at = end + 2;
  }
  return { frames, rest: buffer.slice(at) };
}

/** The session `kraft view logs` reads with no `--session` (`_logs_session`, cli/view.py):
 *  while the item is stopped, the one that stopped it; otherwise the newest. */
export function logSession(d: WorkItemDetail | undefined): WorkerSession | undefined {
  const sessions = d?.worker_sessions ?? [];
  const newest = sessions.at(-1);
  if (!newest) return undefined;
  const stop = d?.status === "needs_human" ? d.stop : null;
  if (!stop) return newest;
  const named = stop.facts?.session_id;
  return sessions.find((s) => s.id === named)
    ?? [...sessions].reverse().find((s) => s.node_id === stop.node && s.hook_point !== "escalation")
    ?? newest;
}

// CSI and OSC, in their 7-bit (ESC [ / ESC ]) and 8-bit (CSI U+009B, OSC U+009D) forms, then any other ESC pair.
const ESCAPES = /(?:\x1b\[|\u009b)[0-?]*[ -/]*[@-~]|(?:\x1b\]|\u009d)[^\x07\x1b\u009c]*(?:\x07|\x1b\\|\u009c)?|\x1b[@-_]?/g;
// C0 but tab, DEL, and C1.
const CONTROLS = /[\x00-\x08\x0a-\x1f\x7f-\u009f]/g;

/** A worker's line as plain text: a terminal must not run the agent's escapes. */
export function clean(s: string): string {
  return s.replace(ESCAPES, "").replace(CONTROLS, "");
}

export function formatLogLine(l: LogLine): string {
  return `${(l.t ?? "").slice(11, 19) || "--:--:--"} ${l.src.padEnd(6)} ${clean(logLineText(l))}`;
}

export type FollowEnd = "ended" | "lost" | "aborted";
export interface FollowOptions {
  /** Consecutive failed connects before giving up. */
  retries?: number;
  /** The wait before reconnect number `failures` (1-based). */
  backoffMs?: (failures: number) => number;
  /** Why the stream was lost, just before `followLog` answers `"lost"`. */
  onLost?: (detail: string) => void;
}

const BACKOFF = (failures: number) => Math.min(1_000 * 2 ** (failures - 1), 8_000);

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(t);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const t = setTimeout(done, ms);
    signal.addEventListener("abort", done);
  });
}

const detailOf = (e: unknown) => {
  const d = (e as { detail?: unknown }).detail;
  return typeof d === "string" ? d : e instanceof Error ? e.message : String(e);
};
const isClientError = (e: unknown) => {
  const status = (e as { status?: unknown }).status;
  return typeof status === "number" && status >= 400 && status < 500;
};

/** Follow a session's log until it ends, reconnecting on a drop.
 *
 *  `open` returns when the server says the session ended and throws on anything
 *  else. Each connect replays the tail, so a line is written only once, by `n`;
 *  the truncation marker (`n: -1`) only before the first real line. Only a new
 *  line resets the failure count: a connect that replays and drops again, or
 *  closes before any line, is one more failure. A 4xx will not get better. */
export async function followLog(
  open: (signal: AbortSignal) => AsyncIterable<LogLine>,
  write: (l: LogLine) => void,
  signal: AbortSignal,
  { retries = 3, backoffMs = BACKOFF, onLost }: FollowOptions = {},
): Promise<FollowEnd> {
  let lastSeen = -1;
  let marked = false;
  let failures = 0;
  for (;;) {
    if (signal.aborted) return "aborted";
    try {
      for await (const l of open(signal)) {
        if (l.n < 0) {
          if (lastSeen < 0 && !marked) write(l);
          marked = true;
          continue;
        }
        if (l.n <= lastSeen) continue;
        lastSeen = l.n;
        failures = 0;
        write(l);
      }
      return signal.aborted ? "aborted" : "ended";
    } catch (e) {
      if (signal.aborted) return "aborted";
      if (isClientError(e) || ++failures >= retries) {
        onLost?.(detailOf(e));
        return "lost";
      }
    }
    await sleep(backoffMs(failures), signal);
  }
}
