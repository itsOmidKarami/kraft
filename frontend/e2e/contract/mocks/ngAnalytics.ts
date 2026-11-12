import type { ItemBundle } from "./fixtures";

/**
 * GET /analytics over the board's own items, by `src/kraft/analytics.py`'s `compute`: its sessions give tokens, cost, wall
 * time, rounds and caps per node and repo; its events give merges, waits at each gate, lead time, repairs and stops. So
 * every number on the page is one the board's items add up to. The board has no sliced items, so no child rolls into a
 * parent here.
 */

const KINDS = ["tokens_in", "tokens_cache_write", "tokens_cache_read", "tokens_out"] as const;
const RANGE_DAYS: Record<string, number | null> = { "7d": 7, "30d": 30, "90d": 90, "8w": 56, all: null };
const BLOCKS = ["gate_requested", "work_item_needs_human"];
const UNBLOCKS = ["gate_approved", "gate_rejected", "work_item_retried", "node_started"];
const RUN_BOUNDARY = ["work_item_created", "work_item_resumed", "pause_requested", "work_item_retried", "work_item_completed", "work_item_abandoned", "work_item_restored", "gate_approved", "gate_rejected"];
const ALWAYS_UNPLANNED = new Set(["orchestrator/infra defect", "preventable dispatch", "budget"]);

const ms = (s: string | null | undefined) => (s ? Date.parse(s) : NaN);
/** `statistics.median`, cut to a whole number as `int()` does. */
const median = (v: number[]) => { const s = [...v].sort((a, b) => a - b); const m = s.length >> 1; return Math.trunc(s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2); };
const round = (x: number, d: number) => Math.round(x * 10 ** d) / 10 ** d;
/** The Monday of `ts`'s week (`_week_start`). */
const weekOf = (ts: string) => { const d = new Date(ts); return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - ((d.getUTCDay() + 6) % 7))).toISOString().slice(0, 10); };

/** An item's (merge, fix-loop, open-MR, CI) nodes, by what each does (`_node_roles`): the forge task each runs, or its `fix_loop`. */
function roles(item: any): Set<string>[] {
  const nodes: any[] = item.materialized_chain ? JSON.parse(item.materialized_chain).chain.nodes : [];
  const tasks = (n: any) => [...(n.tasks ?? []), ...(n.steps ?? []).flatMap((s: any) => s.tasks ?? [])];
  const running = (target: string) => new Set(nodes.filter((n) => tasks(n).some((t) => t.kind === "forge" && t.target === target)).map((n) => n.id));
  return [running("mr.merge"), new Set(nodes.filter((n) => n.fix_loop).map((n) => n.id)), running("mr.open_draft"), running("mr.ci")];
}

/** The stopped-on-a-person spans of one item's events: the gate it waited at (null for a stop that was no gate) and how long. */
function waitSpans(events: any[]): [string | null, number][] {
  const out: [string | null, number][] = [];
  let at: number | null = null; let gate: string | null = null;
  for (const e of events) {
    if (BLOCKS.includes(e.type)) { if (at === null) { at = ms(e.created_at); gate = e.type === "gate_requested" ? e.payload.gate ?? "unknown" : null; } }
    else if (UNBLOCKS.includes(e.type) && at !== null) { out.push([gate, Math.max(0, ms(e.created_at) - at)]); at = null; }
  }
  return out;
}
const humanWait = (events: any[]) => waitSpans(events).reduce((a, [, w]) => a + w, 0);

/** Which stop bucket a `gate_requested` or `work_item_needs_human` is in (`_episode_bucket`); null for any other event. */
function bucket(e: any): string | null {
  if (e.type === "gate_requested") return `gate · ${e.payload.gate ?? "unknown"}`;
  if (e.type !== "work_item_needs_human") return null;
  const reason: string = e.payload.reason ?? "";
  if (e.payload.budget != null) return "budget";
  if (e.payload.capped != null) return "cap breach";
  if (reason.startsWith("resume: current-node session did not resolve cleanly")) return "orchestrator/infra defect";
  if (reason.startsWith("needs_context:") && ["blocked on", "not merged", "depends on"].some((p) => reason.toLowerCase().includes(p))) return "preventable dispatch";
  return "task failure";
}
/** A stop the first run boundary after it shows was cleared with nobody paged (`_resolved_without_human`). */
function selfCleared(events: any[], k: number): boolean {
  const e = events.slice(k + 1).find((x) => RUN_BOUNDARY.includes(x.type));
  return !!e && ((e.type === "work_item_retried" && !!e.payload.escalated) || (e.type.startsWith("gate_") && e.payload.by === "agent"));
}
const touches = (events: any[]) => events.filter((e, k) => { const b = bucket(e); return b !== null && !b.startsWith("gate · ") && (ALWAYS_UNPLANNED.has(b) || !selfCleared(events, k)); }).length;

export function analyticsOf(bundles: ItemBundle[], q: URLSearchParams, nowIso: string) {
  const now = Date.parse(nowIso);
  const range = q.get("range") ?? "7d";
  const days = range in RANGE_DAYS ? RANGE_DAYS[range] : 7;
  const since = days === null ? null : now - days * 86_400_000;
  const repo = q.get("repo"); const template = q.get("template");
  const matches = (b: ItemBundle) => (!repo || b.item.repo === repo) && (!template || (b.item.chain_template ?? "default") === template);
  const all = bundles.filter(matches);
  const picked = all.filter((b) => since === null || ms(b.item.created_at) >= since);
  const completed = picked.filter((b) => b.item.status === "completed");
  const T: Record<string, any> = {
    work_items: picked.length, work_items_run: 0, by_status: {} as Record<string, number>, mrs_merged: 0, wall_ms: 0, human_wait_ms: 0,
    ...Object.fromEntries(KINDS.map((k) => [k, 0])), split_complete: true, cost_usd: 0, cost_complete: true, cost_estimated: false,
    rounds: 0, capped_out: 0, wait_timed_out: 0, time_capped: 0, completed: completed.length, median_lead_ms: 0, human_wait_pct: 0,
    fix_cycles: 0, fix_cycles_capped: 0, rejected_gates: 0, unplanned_touches_per_item: 0, open_mr_to_green_ci_ms: 0,
    completed_prev: since === null ? null : all.filter((b) => b.item.status === "completed" && ms(b.item.created_at) >= since - days! * 86_400_000 && ms(b.item.created_at) < since).length,
  };
  const byNode: Record<string, any> = {}; const byRepo: Record<string, any> = {}; const weekly: Record<string, number> = {};
  const rejected: Record<string, number> = {}; const stops: Record<string, number> = {}; const gates: Record<string, number[]> = {};
  const repairs = new Map<string, number>(); const cappedLoopItems = new Set<string>();
  const leads: number[] = []; const openToGreen: number[] = [];
  let doneWait = 0; let doneTouches = 0;
  for (const b of picked) {
    const i = b.item; const ev: any[] = b.events; const [merge, loops, open, ci] = roles(i);
    T.by_status[i.status] = (T.by_status[i.status] ?? 0) + 1;
    const rr = byRepo[i.repo] ??= { repo: i.repo, items: 0, mrs: 0, tokens: 0, cost_usd: 0, cost_complete: true, cost_estimated: false, done: 0, cycles: 0 };
    rr.items++; if (i.status === "completed") rr.done++;
    const sid = (type: string, f: (p: any) => boolean = () => true) => new Set(ev.filter((e) => e.type === type && f(e.payload)).map((e) => e.payload.session_id));
    const timedOut = sid("external_wait_ended", (p) => p.outcome === "timed_out"); const cut = sid("time_cap_reached"); const waits = sid("external_wait_observed");
    const itemRounds = new Map<string, number>(); const worked = new Set<string>();
    for (const s of b.sessions) {
      const n = byNode[s.node_id] ??= { node: s.node_id, runs: 0, wall_ms: 0, avg_ms: 0, tokens: 0, cost_usd: 0, cost_complete: true, cost_estimated: false, rounds: 0, capped_out: 0, wait_timed_out: 0, time_capped: 0, _rounds: new Set<string>() };
      const tok = KINDS.reduce((a, k) => a + (s[k] ?? 0), 0);
      const wall = s.wall_ms ?? Math.max(0, (ms(s.exited_at) || now) - ms(s.started_at ?? s.created_at));
      const capped = s.status === "capped_out" && !timedOut.has(s.id) && !cut.has(s.id);
      n.runs++; n.wall_ms += wall; n.tokens += tok; n.cost_usd += s.cost_usd ?? 0;
      n.capped_out += +capped; n.wait_timed_out += +timedOut.has(s.id); n.time_capped += +cut.has(s.id);
      T.capped_out += +capped; T.wait_timed_out += +timedOut.has(s.id); T.time_capped += +cut.has(s.id);
      if (capped && loops.has(s.node_id)) cappedLoopItems.add(i.id);
      if (s.cost_usd == null && tok) n.cost_complete = T.cost_complete = rr.cost_complete = false;
      if (s.cost_estimated) n.cost_estimated = T.cost_estimated = rr.cost_estimated = true;
      n._rounds.add(`${i.id}/${s.round ?? 0}`);
      if (!waits.has(s.id) && loops.has(s.node_id) && (s.round ?? 0) >= 1) worked.add(`${s.node_id}/${s.round}`);
      itemRounds.set(s.node_id, Math.max(itemRounds.get(s.node_id) ?? 0, s.round ?? 0));
      for (const k of KINDS) T[k] += s[k] ?? 0;
      if (s.tokens_cache_read == null && tok) T.split_complete = false;
      T.cost_usd += s.cost_usd ?? 0; T.wall_ms += wall; rr.tokens += tok; rr.cost_usd += s.cost_usd ?? 0;
    }
    if (b.sessions.length) { T.work_items_run++; T.rounds += Math.max(...itemRounds.values()); }
    if (worked.size) repairs.set(i.id, worked.size);
    for (const e of ev) {
      if (e.type === "node_completed" && merge.has(e.payload.node_id)) { T.mrs_merged++; rr.mrs++; weekly[weekOf(e.created_at)] = (weekly[weekOf(e.created_at)] ?? 0) + 1; }
      if (e.type === "gate_rejected") rejected[e.payload.gate ?? "unknown"] = (rejected[e.payload.gate ?? "unknown"] ?? 0) + 1;
      const label = bucket(e); if (label) stops[label] = (stops[label] ?? 0) + 1;
    }
    for (const [g, w] of waitSpans(ev)) if (g !== null) (gates[g] ??= []).push(w);
    T.human_wait_ms += humanWait(ev);
    if (i.status !== "completed") continue;
    const first = (type: string, nodes?: Set<string>) => ev.find((e) => e.type === type && (!nodes || nodes.has(e.payload.node_id)))?.created_at;
    const end = first("work_item_completed");
    if (end) leads.push(Math.max(0, ms(end) - ms(first("node_started") ?? i.created_at)));
    const [o, g] = [first("node_started", open), first("node_completed", ci)];
    if (o && g) openToGreen.push(Math.max(0, ms(g) - ms(o)));
    doneWait += humanWait(ev); doneTouches += touches(ev);
  }
  const nRepairs = [...repairs.values()];
  if (nRepairs.length) T.fix_cycles = round(nRepairs.reduce((a, n) => a + n, 0) / nRepairs.length, 1);
  T.fix_cycles_capped = cappedLoopItems.size;
  for (const r of Object.values(byRepo)) {
    const rl = picked.filter((b) => b.item.repo === r.repo && repairs.has(b.item.id)).map((b) => repairs.get(b.item.id)!);
    r.cycles = rl.length ? round(rl.reduce((a, n) => a + n, 0) / rl.length, 1) : 0;
  }
  if (leads.length) T.median_lead_ms = median(leads);
  const lead = leads.reduce((a, n) => a + n, 0);
  if (lead) T.human_wait_pct = Math.min(100, Math.round((100 * doneWait) / lead));
  if (completed.length) T.unplanned_touches_per_item = round(doneTouches / completed.length, 2);
  if (openToGreen.length) T.open_mr_to_green_ci_ms = median(openToGreen);
  T.rejected_gates = Object.values(rejected).reduce((a, n) => a + n, 0);
  const desc = <X,>(rows: X[], key: (x: X) => number) => rows.sort((a, b) => key(b) - key(a));
  return {
    totals: T,
    weekly_merged: Object.keys(weekly).sort().map((w) => ({ week_start: w, n: weekly[w] })),
    by_node: desc(Object.values(byNode).map(({ _rounds, ...n }) => ({ ...n, rounds: _rounds.size, avg_ms: n.runs ? Math.trunc(n.wall_ms / n.runs) : 0 })), (n) => n.cost_usd),
    by_repo: desc(Object.values(byRepo), (r) => r.items),
    rejected_gates_by_gate: desc(Object.entries(rejected).map(([gate, n]) => ({ gate, n })), (r) => r.n),
    gate_waits: desc(Object.entries(gates).map(([gate, v]) => ({ gate, n: v.length, median_ms: median(v), total_ms: v.reduce((a, n) => a + n, 0) })), (r) => r.total_ms),
    stop_reasons: desc(Object.entries(stops).map(([label, n]) => ({ label, n })), (r) => r.n),
  };
}
