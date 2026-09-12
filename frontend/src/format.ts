import type { LogLine } from "./types/work_item";

/** A log line's one-line text. `summary` is a server-rendered stream-json
 *  line; but the server's own summariser (logs.py summary()) falls back to
 *  the raw line for any object shape it does not recognise (e.g.
 *  `{"type":"tool_progress",...}`), so a `summary` equal to the raw text is
 *  not actually a summary. Treat that case the same as no summary at all: an
 *  agent/tool line's raw text is JSON and must not render as
 *  `{"type":"tool_progress",...}` -- fall back to just its `type`. Plain
 *  stdout/sys lines are never JSON, so they pass through untouched. */
export function logLineText(l: LogLine): string {
  if (l.summary && l.summary !== l.text) return l.summary;
  if (l.src === "agent" || l.src === "tool") {
    try {
      const parsed = JSON.parse(l.text);
      if (parsed && typeof parsed === "object" && typeof parsed.type === "string") {
        return parsed.type;
      }
    } catch {
      /* not JSON -- fall through to the raw text */
    }
  }
  return l.text;
}

/** Relative age, compact ("just now", "12m ago", "3h ago", "4d ago"). */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = now - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

/** The same scale pointed forwards, for a deadline rather than a past event. */
export function until(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = Date.parse(iso) - now;
  if (Number.isNaN(ms)) return "";
  if (ms <= 0) return "expired";
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `in ${Math.max(min, 1)}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `in ${hr}h`;
  return `in ${Math.floor(hr / 24)}d`;
}

/** Elapsed span, for "running 4m" and node timings. */
export function elapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
}

/** Wall-clock time of day, for timeline rows and log lines. */
export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Token counts, the way the design writes them: 980, 41.2k, 1.4M. */
export function tokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** USD, with enough places to be useful at agent-run scale.
 *
 * `complete: false` marks a sum that is missing an agent's unreported cost —
 * a floor, not a total. Kraft never invents the difference.
 */
export function usd(n: number, complete = true): string {
  const amount = n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`;
  return complete ? amount : `${amount}+`;
}

/** A repo's own name — the last segment of its path.
 *
 * The absolute path repeats on every board row, in the detail meta line and in
 * both analytics filters. It is machine detail: it wraps to two lines on a
 * phone and buries the title it sits under. Show this, keep the full path in a
 * `title` attribute wherever it is rendered.
 */
export function repoName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

/** Session and work-item statuses in the words the rest of the UI uses.
 *
 * `capped_out` / `needs_human` are database values. The board says "Needs you"
 * and the detail tag says "needs you", so a row two panels down saying
 * `needs_human` reads as a different thing to the person looking at it.
 */
const STATUS_WORDS: Record<string, string> = {
  capped_out: "capped out",
  needs_human: "needs you",
  rate_limited: "rate limited",
  config_error: "config error",
  // A node parked on a pipeline (Kraft-ru98). Says what it is waiting on, so a
  // healthy wait does not read as a stall.
  waiting: "waiting on CI",
};

export function statusWord(status: string): string {
  return STATUS_WORDS[status] ?? status;
}

/** A hook binding's plain-text summary — the Plugins list's "Adapter" column
 *  and the Chains node form's per-task line share this, so both read the
 *  same command the same way. Display formatting with no view of its own. */
export function adapterOf(b: { kind: string; handler?: string; command?: string | string[] }) {
  return b.kind === "builtin"
    ? `builtin · ${b.handler}`
    : Array.isArray(b.command)
      ? b.command.join(" ")
      : (b.command ?? b.kind);
}

/** `item.stop_reason`'s reasoning when the stop was a fix-loop judge
 *  (`kraft.executor.walk`'s `f"judge: {reasoning}"`, mirroring the existing
 *  `"executor crashed: ..."` prefix `CappedCard` already keys off of) --
 *  undefined for any other stop reason. */
export function judgeReasoning(stopReason: string | null | undefined): string | undefined {
  return stopReason?.startsWith("judge:") ? stopReason.slice("judge:".length).trim() : undefined;
}
