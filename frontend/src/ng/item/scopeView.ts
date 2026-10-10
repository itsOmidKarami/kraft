import { elapsed, elapsedBetween } from "../../format";
import type { ScopeRun, SessionStatus, WorkerSession } from "../../types";
import { materialized, taskAt } from "./chainValues";
import { passNow, passOf, standsOn } from "./nodeGraph";
import type { ItemDetail } from "./useItem";

/** The builtin whose task draws as a frame of repositories and scopes once selected. */
export const SCOPE_REF = "kraft.verify_changed_test_scopes";

export type ChipState = "done" | "failed" | "running" | "waiting" | "skipped";
export type Chip = {
  /** The repository and command: what a scope is across rounds, and the URL's `scope`. */
  key: string;
  /** What the chip reads: its command, trimmed to about 28 characters, or `setup · <area>`. */
  name: string;
  command: string;
  /** The scope's paths, when the repo's table still names it. */
  paths?: string;
  area?: string;
  setup?: boolean;
  state: ChipState;
  meta: string;
  /** Picked this round and not the one before. */
  fresh?: boolean;
  session?: WorkerSession;
};
export type RepoState = "done" | "failed" | "running" | "waiting" | "unreached";
export type RepoRow = { id: string | null; name: string; state: RepoState; note: string; chips: Chip[]; /** The round runs on and has not recorded its picks: what it dropped is not known yet. */ choosing?: true };
export type ScopesView = { path: string; round: number; execution: "sequential" | "parallel"; rows: RepoRow[] };

/** A repository's row in the open frame: 40px, or in parallel as tall as its stacked chips. */
export const rowHeight = (row: RepoRow, execution: ScopesView["execution"]) => (execution === "parallel" ? Math.max(40, row.chips.length * 30 + 6) : 40);
/** The frame's height: its 40px header, 14px of padding, the rows and 8px between them. */
export const frameHeight = (v: ScopesView) => 40 + 14 + v.rows.reduce((t, r) => t + rowHeight(r, v.execution), 0) + 8 * (v.rows.length - 1);

/** About how wide a chip draws: 11.5px type, its command in monospace (~7px a character), its meta in the UI face
 *  (~6.5px), inside 8px of padding a side, a running chip's 1.5px border, the dot and 6px gaps. A running chip's
 *  clock is counted at its widest, `running · 59m 59s`, so the frame does not widen as the seconds tick, and so is a
 *  waiting one's, which it becomes when it starts. */
const chipWidth = (c: Chip) => {
  const meta = c.state === "running" || c.state === "waiting" ? Math.max(c.meta.length, 17) : c.meta.length;
  return 19 + 7 + 6 + c.name.length * 7 + (meta ? 6 + meta * 6.5 : 0) + (c.fresh ? 6 + 28 : 0);
};
/** A row's chips: in a line, each after the first behind a 6px gap, an arrow and another 6px; forked, the widest
 *  beside its 10px tick. */
const chipsWidth = (row: RepoRow, execution: ScopesView["execution"]) => {
  const w = row.chips.map(chipWidth);
  if (!w.length) return 0;
  return execution === "parallel" ? 2 + 10 + 6 + Math.max(...w) : w.reduce((t, x) => t + x, 0) + (w.length - 1) * (6 + 12 + 6) + 4;
};
/** The words beside the frame's title. One repository is not a sequence of them, so it is not called one. */
export const subtitle = (v: ScopesView) => `round ${v.round} · ${v.rows.length > 1 ? "repos in order · " : ""}scopes ${v.execution === "parallel" ? "in parallel" : "in order"}`;
/** About how wide the frame's header draws: 24px of padding, the task's name in 12px monospace, the subtitle in the
 *  11px UI face, the close button, and 10px between them. */
const headWidth = (v: ScopesView) => 24 + v.path.split(".").at(-1)!.length * 7.3 + 10 + subtitle(v).length * 5.3 + 10 + 58;
/** The frame's width: wide enough for its header and its longest row of chips, as it is tall enough for its rows,
 *  so nothing in it scrolls under a canvas whose wheel pans. A row is 14px of padding a side, the 20px ring, the
 *  112px repository column and the 10px gaps between them; rounded up to 20px, with 16 to spare. One repository
 *  has no ring and no column to draw. */
export const frameWidth = (v: ScopesView) => {
  const widest = Math.max(0, ...v.rows.map((r) => chipsWidth(r, v.execution)));
  const row = 28 + (v.rows.length === 1 ? 0 : 20 + 10 + 112 + 10) + widest;
  return Math.ceil((Math.max(row, headWidth(v)) + 16) / 20) * 20;
};

/** Whether the frozen chain's task at `path` is the changed-test-scope builtin. */
export const isScopeTask = (item: ItemDetail, path: string) => {
  const m = materialized(item);
  return !!m && taskAt(m, path)?.ref === SCOPE_REF;
};

export const scopeKey = (repository: string | null | undefined, command: string) => `${repository ?? ""}:${command}`;
/** A command as a chip reads it: about 28 characters, the rest on the tooltip. */
export const trim = (c: string, n = 28) => (c.length > n ? `${c.slice(0, n - 1)}…` : c);
const basename = (p: string) => p.replace(/\/+$/, "").split("/").at(-1) || p;

/** The runs of the changed-test-scope task at `path`, in the pass its node is on. A run's round is its session's as
 *  the node's pass reads it: a re-measure after `on_failure` (stamped -1) is the round's own. */
const runsOf = (item: ItemDetail, path: string): ScopeRun[] => {
  const node = path.split(".")[0];
  const top = passNow(item, node);
  const read = new Map(passOf(item, node).map((s) => [s.id, s.round]));
  return (item.scope_runs ?? []).filter((r) => r.hook_point === path && (r.pass ?? 1) === top).map((r) => ({ ...r, round: (r.session_id ? read.get(r.session_id) : undefined) ?? r.round }));
};

/** The repositories a fanned-out task visits, in the order it does (`dispatch._fan_out`): a workspace's root, then
 *  its members; one repository, or none selected, is one run in the item's own checkout, named for it. */
export function reposOf(item: ItemDetail): { id: string | null; name: string }[] {
  const t = materialized(item)?.target;
  const ids = t?.kind === "workspace" ? [...(t.root ? [t.root] : []), ...Object.values(t.mounts ?? {}).map((m) => m.repository)] : [];
  return ids.length ? ids.map((id) => ({ id, name: id })) : [{ id: null, name: basename(item.repo) }];
}

const CHIP: Record<SessionStatus, ChipState> = {
  running: "running", pending: "waiting", paused: "waiting", needs_context: "waiting", rate_limited: "waiting", waiting: "waiting",
  done: "done", done_with_concerns: "done",
  failed: "failed", capped_out: "failed", config_error: "failed", unknown: "failed", conflict: "failed", infra: "failed", infra_stop: "failed",
};

function chipState(s: WorkerSession | undefined, passed: boolean | null): ChipState {
  if (s) return CHIP[s.status] ?? "waiting";
  return passed === null ? "running" : passed ? "done" : "failed";
}

function metaOf(state: ChipState, s: WorkerSession | undefined, now: number): string {
  const took = s?.wall_ms != null ? elapsed(s.wall_ms) : undefined;
  if (state === "running") return s?.started_at ? `running · ${elapsedBetween(s.started_at, null, now)}` : "running";
  if (state === "waiting") return s?.status === "paused" ? "paused" : "waiting";
  if (state === "failed") return took ? `failed · ${took}` : "failed";
  return took ?? "";
}

const byOrder = (a: Chip & { order?: number }, b: Chip & { order?: number }) => (a.order ?? Infinity) - (b.order ?? Infinity);

/** The scopes the changed-test-scope task at `path` ran in `round` (1-based), per repository in fan-out order,
 *  each beside what the round before it picked. The task, by `scope_runs`: the latest run of a command in a
 *  repository is the one a round shows (a retry runs it again in the same round). */
export function scopesView(item: ItemDetail, path: string, round: number, now: number): ScopesView {
  const runs = runsOf(item, path);
  const sessions = new Map(item.worker_sessions.map((s) => [s.id, s]));
  const sessionOf = (r: ScopeRun) => (r.session_id ? sessions.get(r.session_id) : undefined);
  const m = materialized(item);
  const execution = taskAt(m ?? { chain: { nodes: [] } }, path)?.execution === "parallel" ? "parallel" : "sequential";
  // Runs fanned out over a workspace carry their repository, even a workspace of one; otherwise there is one run, in the item's own checkout.
  const repos = runs.some((r) => r.repository) || !runs.length ? reposOf(item) : [{ id: null, name: basename(item.repo) }];
  const last = (rs: ScopeRun[]) => [...new Map(rs.map((r) => [scopeKey(r.repository, r.command), r])).values()];
  const inRound = (n: number, repo: string | null) => last(runs.filter((r) => r.round === n - 1 && r.repository === repo));
  // The round is still going while one of its commands runs, or the node it belongs to is the one running.
  const node = path.split(".")[0];
  const latest = Math.max(0, ...runs.map((r) => r.round + 1));
  const going = runs.some((r) => r.passed === null) || item.worker_sessions.some((s) => s.hook_point === path && ["running", "pending"].includes(s.status)) || (standsOn(item, node) && item.display_status === "running");
  const liveRound = going && round >= latest;
  const rows: RepoRow[] = [];
  let failedBefore: string | null = null;
  for (const repo of repos) {
    const id = repo.id;
    const picked = inRound(round, id);
    const before = round > 1 ? inRound(round - 1, id) : [];
    const sameAs = (a: ScopeRun, b: ScopeRun) => scopeKey(a.repository, a.command) === scopeKey(b.repository, b.command);
    const chip = (r: ScopeRun, skipped = false): Chip & { order?: number } => {
      const session = sessionOf(r);
      const state = skipped ? "skipped" : r.pending ? "waiting" : chipState(session, r.passed);
      return {
        key: scopeKey(r.repository, r.command),
        name: r.setup ? `setup · ${r.area ?? trim(r.command)}` : trim(r.command),
        command: r.command,
        paths: r.scope,
        area: r.area,
        setup: r.setup,
        state,
        meta: skipped ? "not picked" : metaOf(state, session, now),
        // New against the round before alone, and only in a repository that ran then: a repository first reached now is not a scope picked anew.
        fresh: round > 1 && !skipped && before.length > 0 && !before.some((b) => sameAs(b, r)) ? true : undefined,
        session,
        order: r.order,
      };
    };
    const ran = picked.map((r) => chip(r));
    // What the round before ran and this one did not: once this one has chosen all of its scopes, which is when it ends or records its picks.
    const choosing = liveRound && !picked.some((r) => r.selected);
    const dropped = round > 1 && !choosing && ran.length ? before.filter((b) => !picked.some((r) => sameAs(r, b))).map((b) => chip(b, true)) : [];
    const chips = [...ran, ...dropped].sort(byOrder).map(({ order: _order, ...c }) => c);
    const failed = ran.find((c) => c.state === "failed");
    let state: RepoState, note: string;
    if (failed) {
      const t = ran.reduce((a, c) => a + (c.session?.wall_ms ?? 0), 0);
      [state, note] = ["failed", t ? `failed · ${elapsed(t)}` : "failed"];
      failedBefore ??= repo.name;
    } else if (ran.length && ran.every((c) => c.state === "waiting")) [state, note] = ["waiting", "waiting"];
    else if (ran.some((c) => c.state === "running" || c.state === "waiting")) [state, note] = ["running", "running"];
    else if (ran.length) {
      const took = ran.map((c) => c.session?.wall_ms ?? 0);
      const t = execution === "parallel" ? Math.max(...took) : took.reduce((a, b) => a + b, 0);
      [state, note] = ["done", t ? `done · ${elapsed(t)}` : "done"];
    } else if (failedBefore) [state, note] = ["unreached", `not reached · ${failedBefore} failed`];
    else if (liveRound) [state, note] = ["waiting", "waiting"];
    else [state, note] = ["unreached", "not reached"];
    rows.push({ id, name: repo.name, state, note, chips, ...(choosing && { choosing }) });
  }
  return { path, round, execution, rows };
}

/** One scope across the other rounds, for the scope pane: "1: passed · 3: not picked". A round it ran in says how it went;
 *  one its repository ran in without it says "not picked"; one that never reached the repository says so. `round` itself
 *  is left out, as are rounds the node has not got to, and those before `from`, the round the pass started at. */
export function otherRounds(item: ItemDetail, path: string, key: string, round: number, from = 1): string[] {
  const all = runsOf(item, path);
  const [repo, ...rest] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
  const command = rest.join(":");
  const latest = Math.max(0, ...all.map((r) => r.round + 1));
  const out: string[] = [];
  for (let n = from; n <= latest; n++) {
    if (n === round) continue;
    const here = all.filter((r) => r.round === n - 1 && (r.repository ?? "") === repo);
    const ran = here.filter((r) => r.command === command).at(-1);
    out.push(`${n}: ${ran ? (ran.pending ? "waiting" : ran.passed === null ? "running" : ran.passed ? "passed" : "failed") : here.length ? "not picked" : "repo not reached"}`);
  }
  return out;
}

/** The session that speaks for a changed-test-scope task in a round: a scope that failed, else one still going. The
 *  task is not done while one of its scopes is not, whichever ran last; undefined when every scope passed. */
export function scopeLead(view: ScopesView): WorkerSession | undefined {
  const ran = view.rows.flatMap((r) => r.chips).filter((c) => c.session);
  return (ran.find((c) => c.state === "failed") ?? ran.find((c) => c.state === "running" || c.state === "waiting"))?.session;
}

/** A scope's runs in a round, oldest first: more than one when it was run again inside the round. */
export const scopeRuns = (item: ItemDetail, path: string, key: string, round: number): WorkerSession[] =>
  passOf(item, path.split(".")[0]).filter((s) => s.hook_point === path && s.round === round - 1 && !!s.command && scopeKey(s.repository, s.command) === key).sort((a, b) => a.attempt - b.attempt || a.created_at.localeCompare(b.created_at));

/** A chip as one of its scope's runs reads: an earlier run of it in the round, picked. */
export function chipOfRun(chip: Chip, s: WorkerSession, now: number): Chip {
  const state = chipState(s, null);
  return { ...chip, session: s, state, meta: metaOf(state, s, now) };
}

export type ScopeMiss = "not picked" | "not reached" | "not found" | "still choosing";
/** One scope of the task in a round, by its key: its repository's row and its chip, or why the round has none for it.
 *  A key no round of the pass ran is not a scope of the task ("not found": a stale or hand-made link); a round that
 *  runs on and has not recorded its picks has dropped nothing yet ("still choosing"). */
export function scopeAt(item: ItemDetail, path: string, key: string, view: ScopesView): { row?: RepoRow; chip?: Chip; command: string; miss?: ScopeMiss; why?: string } {
  // A key is `<repository or nothing>:<command>`; one with no colon names no repository.
  const at = key.indexOf(":");
  const [repo, command] = [key.slice(0, Math.max(at, 0)), key.slice(at + 1)];
  for (const row of view.rows) {
    const chip = row.chips.find((c) => c.key === key);
    if (chip) return { row, chip, command };
  }
  const row = view.rows.find((r) => (r.id ?? "") === repo);
  if (!runsOf(item, path).some((r) => scopeKey(r.repository, r.command) === key)) return { row, command, miss: "not found", why: "Not found: this task ran no scope with this command." };
  if (row?.choosing) return { row, command, miss: "still choosing", why: "Still choosing: this round has not picked its scopes yet." };
  if (row && row.chips.length > 0) return { row, command, miss: "not picked", why: "Not picked: no changed path reaches it this round." };
  return { row, command, miss: "not reached", why: "Its repository was not reached this round." };
}

/** A chip's state in the words a scope's subtitle uses: "passed 24s", "failed 1m", "running", "not picked". */
export function stateWords(c: Chip): string {
  if (c.state === "skipped") return "not picked";
  const took = c.session?.wall_ms != null ? ` ${elapsed(c.session.wall_ms)}` : "";
  if (c.state === "done") return `passed${took}`;
  if (c.state === "failed") return `failed${took}`;
  return c.meta || (c.state === "waiting" ? "waiting" : "running");
}

/** The status row: the same, with a dot before the time, and why a dropped scope is. */
export const statusWords = (c: Chip) => (c.state === "skipped" ? "not picked · no changed path reaches it this round" : stateWords(c).replace(/^(passed|failed) (?=\d)/, "$1 · "));

/** How a round went, in a line each: the scopes it ran that passed, and the repositories it got to. */
export function roundWords(view: ScopesView): { scopes: string; repos: string } {
  const chips = view.rows.flatMap((r) => r.chips).filter((c) => c.state !== "skipped");
  const reached = view.rows.filter((r) => r.state !== "unreached" && r.state !== "waiting").length;
  return {
    scopes: `${chips.filter((c) => c.state === "done").length} of ${chips.length} scopes passed`,
    repos: `${reached} of ${view.rows.length} reached · run in order, stop at the first failure`,
  };
}
