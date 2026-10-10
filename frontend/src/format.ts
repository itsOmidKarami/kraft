import type { KraftEvent, LogLine, WorkerSession } from "./types";

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

/** Elapsed span, for "running 4m" and node timings: `52s`, `4m`, `1h 21m`,
 *  `2d 3h`. Clamped at zero — a start stamped ahead of this machine's clock
 *  reads "0s", never "-31317s". */
export function elapsed(ms: number): string {
  const s = Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : 0;
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

/** The span between two timestamps; `to = null` means now. Every duration on
 *  the item page (hero, split header, stage-graph tooltip, phone stage list)
 *  goes through this with the same inputs, so they cannot disagree. */
export function elapsedBetween(
  fromIso: string | null | undefined,
  toIso: string | null = null,
  now = Date.now(),
): string {
  const from = fromIso ? Date.parse(fromIso) : NaN;
  const to = toIso ? Date.parse(toIso) : now;
  return Number.isNaN(from) || Number.isNaN(to) ? "0s" : elapsed(to - from);
}

/** A node's run time: from its latest `node_started` to its `node_completed`,
 *  or — while it has not completed — to when its latest session exited. The
 *  clock is frozen once that session exits (gated, paused, capped, question,
 *  failed); only a session still running or pending keeps it counting
 *  (`to: null`). Escalation turns are conversation about the node, not its
 *  run, and do not restart the clock. Waiting on a person is shown separately
 *  (`waitingSince`). */
export function nodeRunSpan(
  nodeId: string | null | undefined,
  events: KraftEvent[],
  sessions: WorkerSession[],
): { from: string; to: string | null } | null {
  if (!nodeId) return null;
  let startIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "node_started" && events[i].payload.node_id === nodeId) {
      startIdx = i;
      break;
    }
  }
  const runs = sessions
    .filter((s) => s.node_id === nodeId && s.hook_point !== "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const from = startIdx >= 0 ? events[startIdx].created_at : (runs[0]?.started_at ?? null);
  if (!from) return null;
  const completed = events
    .slice(startIdx + 1)
    .find((e) => e.type === "node_completed" && e.payload.node_id === nodeId);
  if (startIdx >= 0 && completed) return { from, to: completed.created_at };
  // Liveness, not recency (Kraft-s7c04.47). `runs` is ordered by `created_at`,
  // and a node that fans a fast builtin alongside a slow agent creates both in
  // the same tick -- `implementation` creates `on.repos.scan` 0.5 ms after
  // `on.implementation.start`, and it exits in 61 ms. Taking the last-created
  // session froze the node's clock 62 ms after it started, so a 75-minute run
  // read "0s" for its whole duration. Any run still going keeps the span open;
  // when none is, the span ends at the LATEST exit rather than at whichever
  // session happened to be created last.
  if (runs.length && !runs.some((s) => s.status === "running" || s.status === "pending")) {
    const lastExit = runs.reduce<string | null>(
      (acc, s) => (s.exited_at && (!acc || s.exited_at > acc) ? s.exited_at : acc),
      null,
    );
    return { from, to: lastExit ?? from };
  }
  return { from, to: null };
}

/** A 32-hex id as `first8…last5` (README §5): short enough never to be
 *  ellipsized, both ends kept so two ids still tell apart. Shorter ids pass
 *  through. Render the full id in a `title` beside it. */
export function shortId(id: string): string {
  return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-5)}` : id;
}

/** Every 32-hex id inside a string, shortened (W5.1): session summaries are
 *  titled `Session <id>` or the bare id. */
export const shortIds = (s: string) => s.replace(/\b[0-9a-f]{32}\b/g, shortId);

/** Markdown down to its words (W5.3): drop link targets, heading / quote /
 *  list markers, bold and code ticks. Single `_` stays -- it is in every
 *  snake_case identifier. ponytail: regex, not a markdown parser. */
export const plainMarkdown = (s: string) =>
  s
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/\*\*|__|`|\*/g, "");

/** A title that is only an id: `8cbfe6e2…6357d`, a 32-hex, `Session <id>`. */
const ID_TITLE = /^[0-9a-f]{8}…?[0-9a-f]{4,}$/;

/** The first line of a body a person would call its opening: past front
 *  matter, headings, quotes, rules and table rows, as plain text, cut at its
 *  first sentence end or 90 characters. */
function openingLine(md: string): string | null {
  const lines = md.split("\n");
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i++;
  if (lines[i]?.trim() === "---") {
    i++;
    while (i < lines.length && lines[i].trim() !== "---") i++;
    i++;
  }
  for (; i < lines.length; i++) {
    const raw = lines[i].trim();
    if (!raw || /^#{1,6}(\s|$)/.test(raw) || raw.startsWith(">") || raw.startsWith("|") || /^(-{3,}|\*{3,}|```|~~~)/.test(raw)) continue;
    const text = plainMarkdown(raw).trim();
    if (!text) continue;
    const sentence = text.match(/^(.+?[.!?])(?:\s|$)/)?.[1] ?? text;
    return sentence.length > 90 ? `${sentence.slice(0, 89).trimEnd()}…` : sentence;
  }
  return null;
}

/** A document's title as a person reads it (W11 · H), one rule for the
 *  Documents list, the document pane and modal, and search results. A real
 *  title passes through. An empty one, an id, `Session <id>` or the session's
 *  own id is replaced by the body's opening line (or a search snippet's),
 *  prefixed `Session · ` only when that line is under 12 characters; with no
 *  body to read, `Session · <hook>`. Never a bare id. */
export function docTitle(d: {
  title?: string | null;
  content?: string | null;
  kind?: string | null;
  hook_point?: string | null;
  node_id?: string | null;
  worker_session_id?: string | null;
}): string {
  const title = (d.title ?? "").trim();
  const bare = title.replace(/^session\s+/i, "");
  const onlyAnId = !title || ID_TITLE.test(bare) || (!!d.worker_session_id && bare === d.worker_session_id);
  if (!onlyAnId) return shortIds(title);
  const line = d.content ? openingLine(d.content) : null;
  if (line) return line.length < 12 ? `Session · ${line}` : line;
  return `Session · ${d.hook_point ?? d.node_id ?? d.kind ?? "summary"}`;
}

// ponytail: without the item's own bead id, a bead id is guessed as
// `Capitalised-xxxx` -- tight enough for Kraft ids; pass the item for an exact match.
const BEAD_ID = "[A-Z][A-Za-z]*-[a-z0-9]{4,6}";
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A document title with what its row already says taken out (W13 · B.3): a
 *  leading/trailing bead id -- `(Kraft-df4tc)`, `— Kraft-df4tc …`,
 *  `Kraft-df4tc:` -- and the item's own title. `""` when under 3 characters
 *  remain. */
export function cleanTitle(
  doc: { title?: string | null },
  item?: { title?: string | null; bead_id?: string | null } | null,
): string {
  const bead = item?.bead_id ? escapeRe(item.bead_id) : BEAD_ID;
  const flags = item?.bead_id ? "i" : "";
  let t = doc.title ?? "";
  t = t.replace(new RegExp(`\\s+[—–]\\s*${bead}\\b.*$`, flags), "");
  t = t.replace(new RegExp(`\\(\\s*${bead}\\s*\\)`, `g${flags}`), " ");
  t = t.replace(new RegExp(`^\\s*${bead}\\s*:\\s*`, flags), "");
  // Whole words only: an item titled "T" must not eat the t out of "the".
  const own = item?.title?.trim();
  if (own) t = t.replace(new RegExp(`(^|\\W)${escapeRe(own)}(?=\\W|$)`, "i"), "$1");
  t = t.replace(/\s+/g, " ").replace(/^[\s—–·:-]+|[\s—–·:-]+$/g, "");
  return t.length < 3 ? "" : t;
}

/** Wall-clock time of day, for timeline rows and log lines. */
export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** A session's or a rollup's token columns, as the API sends them. */
export interface TokenUse {
  tokens_in?: number | null;
  /** null while a session is live: only its exit reports output. */
  tokens_out?: number | null;
  tokens_cache_write?: number | null;
  tokens_cache_read?: number | null;
  /** False on a rollup holding a session from before cache was counted apart. */
  split_complete?: boolean;
}

/** Every token a run was billed for: cache reads and writes are input too, and
 *  are most of a long agent run (`Usage.total` in usage.py). */
export function tokenTotal(u: TokenUse): number {
  return (u.tokens_in ?? 0) + (u.tokens_out ?? 0) + (u.tokens_cache_write ?? 0) + (u.tokens_cache_read ?? 0);
}

const unsplit = (u: TokenUse) => u.split_complete === false || u.tokens_cache_read == null;

/** Token counts, the way the design writes them: 980, 41.2k, 1.4M. */
export function tokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** A run's tokens by what they cost: "40k new + 900k cached". Cache reads are
 *  billed at a fraction of fresh input, so the total alone overstates a run and
 *  the uncached count alone hides most of it. One number when there is nothing
 *  cached, or when cache was not counted apart. */
export function tokenText(u: TokenUse): string {
  const cached = u.tokens_cache_read ?? 0;
  if (!cached || unsplit(u)) return tokens(tokenTotal(u));
  return `${tokens(tokenTotal(u) - cached)} new + ${tokens(cached)} cached`;
}

/** Every kind apart, as `kraft view show` words it: what the figure's tooltip reads.
 *  No output beside tokens spent is a session still running (a rollup sums its
 *  unknown as 0), so it reads as not known rather than as a false zero. */
export function tokenTip(u: TokenUse): string {
  const out = u.tokens_out ? `${tokens(u.tokens_out)} out` : "out not known yet";
  const old = "cache not split on older sessions";
  if (u.tokens_cache_read == null) return `${tokens(u.tokens_in ?? 0)} in (${old}) · ${out}`;
  const all = `${tokens(u.tokens_in ?? 0)} in · ${tokens(u.tokens_cache_write ?? 0)} cache write · ${tokens(u.tokens_cache_read)} cache read · ${out}`;
  return u.split_complete === false ? `${all} (${old})` : all;
}

/** USD, with enough places to be useful at agent-run scale.
 *
 * `complete: false` marks a sum that is missing an agent's unreported cost —
 * a floor, not a total. Kraft never invents the difference.
 *
 * `estimated: true` (Kraft-wz83s) marks a running session's guess, priced
 * from its live tokens rather than reported by the agent — `~$12 (est.)`
 * instead of `$12` or `$12+`, since neither of those says "this number will
 * change". Takes precedence over `complete`: an estimate is never a floor,
 * it's a guess the exit envelope replaces outright.
 */
export function usd(n: number, complete = true, estimated = false): string {
  const amount = n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`;
  if (estimated) return `~${amount} (est.)`;
  return complete ? amount : `${amount}+`;
}

/** A typed dollar amount, or NaN for what it cannot read for sure. A lone
 *  comma is a decimal point (a comma-decimal locale, iOS's decimal keypad:
 *  "0,5"); with both, the last is the decimal point and the other groups
 *  thousands ("1,000.50", "1.000,50"); several of one alone group thousands.
 *  "1,000" and "1.500" are refused: a thousand to one reader, one (or one and
 *  a half) to another, and it read "1,000" as $1.00 (R12b-10). Only digits and
 *  one point survive: Number() also took "Infinity", "0x10" and "1e3". */
export function dollars(text: string): number {
  const t = text.trim().replace(/^\$\s*/, "");
  if (/^[1-9]\d{0,2}[.,]\d{3}$/.test(t)) return NaN;
  const grouped = (int: string, sep: string) => new RegExp(`^[1-9]\\d{0,2}(\\${sep}\\d{3})+$`).test(int);
  const seps = t.match(/[.,]/g) ?? [];
  let plain = t;
  if (new Set(seps).size === 2) {
    const dec = t.lastIndexOf(",") > t.lastIndexOf(".") ? "," : ".";
    const at = t.lastIndexOf(dec);
    const int = t.slice(0, at);
    plain = grouped(int, dec === "," ? "." : ",") ? `${int.replace(/[.,]/g, "")}.${t.slice(at + 1)}` : "";
  } else if (seps.length > 1) plain = grouped(t, seps[0]!) ? t.replace(/[.,]/g, "") : "";
  else plain = t.replace(",", ".");
  return /^(\d+(\.\d+)?|\.\d+)$/.test(plain) ? Number(plain) : NaN;
}

/** An amount as a field starts with it, read back by `dollars` as the same
 *  amount: a cap of $1.234 written "1.234" is the shape `dollars` refuses as
 *  ambiguous, so re-saving it unchanged failed (r12 review); "1.2340" is not. */
export const dollarsText = (n: number): string => {
  const s = String(n);
  return /^[1-9]\d{0,2}\.\d{3}$/.test(s) ? `${s}0` : s;
};

/** What Copy path puts on the clipboard: a scanned file's absolute path, as 1.4's did
 *  (`path` is relative to `repo`); any other document's own path. */
export const copyablePath = (d: { path: string; repo?: string; origin?: string }): string =>
  d.origin === "git_scan" && d.repo && !d.path.startsWith("/") ? `${d.repo.replace(/\/$/, "")}/${d.path}` : d.path;

/** "1 line", "2 lines": the count and its noun, `many` where adding an s is wrong. */
export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/** "1 line", "2 lines". */
export const lineCount = (n: number): string => plural(n, "line");

/** What to say when `dollars` refused what was typed. */
export const DOLLARS_HINT = "Type the amount plainly, like 1000 or 1.5.";

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

/** A document's body as the viewer shows it (W8.2). The header already names
 *  the document, so a leading H1 that repeats the title goes; so does any
 *  heading with nothing under it before the next heading of its level or
 *  higher (a "## Summary" an agent never filled in). Fenced code is left
 *  alone. ponytail: line scan, not a markdown parser. */
export function docBody(md: string, title?: string): string {
  const lines = md.split("\n");
  const heading = (l: string) => l.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
  const fence = /^\s*(```|~~~)/;
  let at = 0;
  while (at < lines.length && !lines[at].trim()) at++;
  const first = heading(lines[at] ?? "");
  if (first && first[1].length === 1 && title && first[2].trim() === title.trim()) lines.splice(at, 1);
  const out: string[] = [];
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (fence.test(lines[i])) inFence = !inFence;
    const h = inFence ? null : heading(lines[i]);
    if (h) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      const next = j < lines.length ? heading(lines[j]) : null;
      if (j >= lines.length || (next && next[1].length <= h[1].length)) continue;
    }
    out.push(lines[i]);
  }
  return out.join("\n");
}
