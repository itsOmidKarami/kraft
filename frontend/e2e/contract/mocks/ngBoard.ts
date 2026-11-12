import { hex, repo, t, type ItemBundle, type Variant } from "./fixtures";
import draftViews from "./draftViews.json" with { type: "json" };
import { chainOf } from "./ngChain";
import { buildNeverStarted, buildNgItem, type NgScenario } from "./ngItems";
import { cacheRead } from "./nodeRecipe";
import { CONTENT, mrOf, planOf } from "./ngContent";
import { NG_NOW } from "./clock";

/**
 * The board's list (ux2-W6): one row per kind of row AreaBoard.dc.html
 * draws, each built on ngItems.ts's chain so the peek has a full detail. Served
 * by GET /work-items only when a case asks (`MockOptions.ngBoard`), so no
 * shipped cell and no ng-item cell changes. Ages count back from NG_NOW
 * (T0 + 130 minutes). The list holds ids: GET /work-items reads each row from its bundle
 * (transitions.ts `listRow`, whose `stop` carries no `facts`, as W4's list sends it).
 */

/** `fresh`: filed paused and never started, so it has no history at all (only `work_item_created`), whatever `sc` ran.
 *  `spend`: a capped row its spend cap stopped (ngItems.ts). */
type Row = { sc: NgScenario; title: string; repo: string; bead: string; age: number; chain?: string; fresh?: boolean; spend?: boolean; tweak?: (i: any, b: ItemBundle) => void };

const R = (name: string) => `/Users/dev/code/${name}`;
const LONG_R = (name: string) => `/Volumes/Work/clients/acme-corporation/monorepo/services/${name}-with-a-long-checkout-path`;

/** The capped sample's session ran to its end instead: no time cap was reached, and it ended after 90 minutes, inside
 *  the 96 every successful implementer run on record took at most (`config/library.yaml`), half an hour short of the
 *  implementer's 120-minute cap. Its exit and what follows it move back with it, and its node's and the item's wall time
 *  with its own. */
function noTimeCap(i: any, b: ItemBundle) {
  const cut = 30 * 60_000;
  const s = b.sessions.find((x) => x.status === "capped_out")!;
  b.events = b.events.filter((e) => e.type !== "time_cap_reached");
  const exit = b.events.findIndex((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id);
  for (const e of b.events.slice(exit)) e.created_at = new Date(Date.parse(e.created_at) - cut).toISOString();
  Object.assign(s, { wall_ms: s.wall_ms - cut, exited_at: b.events[exit].created_at });
  b.events[exit].payload.wall_ms = s.wall_ms;
  for (const r of [i.usage.total, ...i.usage.by_node.filter((x: any) => x.node === s.node_id)]) r.wall_ms -= cut;
  b.events.forEach((e, n) => { e.seq = n + 1; });
}

/** The capped sample's implementer ran to its end instead: it reported the rest of its plan's tasks before it exited,
 *  each stamped between the last it had reported and its exit, so it committed every task's file (ngContent.ts
 *  `changedFiles`). A moved checkout stops the item after the task ran (`inplace.check_task`), so it does not cut the
 *  plan short. */
function finishedPlan(b: ItemBundle) {
  const told = b.events.filter((e) => e.type === "plan_progress");
  const last = told.at(-1)!;
  const s = b.sessions.find((x) => x.hook_point === "implementation.main.implement")!;
  const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id)!;
  const plan = planOf(b.item.materialized_chain, b.content);
  const rest = plan.slice(told.length);
  const from = Date.parse(last.created_at);
  const step = (Date.parse(exit.created_at) - from) / (rest.length + 1);
  b.events.splice(b.events.indexOf(last) + 1, 0, ...rest.map((title, k) => ({ ...last, payload: { ...last.payload, task: told.length + k + 1, title }, created_at: new Date(from + step * (k + 1)).toISOString() })));
  b.events.forEach((e, n) => { e.seq = n + 1; });
}

/** The capped sample stopped instead by its $5 spend cap, on verification before its code review launched (ngItems.ts,
 *  `spend`): its stop, its last event and its spend all say so. `board._stop` names a budget stop's scope; `work_item` is
 *  the item's own cap, the one `/budget/raise` takes. */
function spendCapped(i: any, b: ItemBundle) {
  const reason = "budget cap reached: $5.00 spent on this work item, cap $5.00. Nothing new was started; a running agent was not interrupted.";
  // A budget stop names its scope (`board._stop`).
  i.stop = { ...i.stop, kind: "budget", reason, facts: {}, scope: "work_item" };
  const last = [...b.events].reverse().find((e) => e.type === "work_item_needs_human")!;
  last.payload = { node_id: last.payload.node_id, reason, kind: "budget", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } };
  // Its sessions' costs scaled to the $5 it spent, and its usage read off them again.
  const was = b.sessions.reduce((a, s) => a + (s.cost_usd ?? 0), 0);
  // Only an agent's session has a cost: a test command's has none, and stays so. Its cache reads are what the new cost
  // leaves, and its exit says what it now reports.
  for (const s of b.sessions) if (s.cost_usd != null) {
    s.cost_usd = Math.round((s.cost_usd * 5 / was) * 10_000) / 10_000;
    s.tokens_cache_read = cacheRead(s);
    const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id);
    if (exit) Object.assign(exit.payload, { cost_usd: s.cost_usd, tokens_cache_read: s.tokens_cache_read });
  }
  const sum = (ss: any[]) => Math.round(ss.reduce((a, s) => a + (s.cost_usd ?? 0), 0) * 10_000) / 10_000;
  const reads = (ss: any[]) => ss.reduce((a, s) => a + (s.tokens_cache_read ?? 0), 0);
  i.usage = { ...i.usage, total: { ...i.usage.total, cost_usd: sum(b.sessions), tokens_cache_read: reads(b.sessions) }, by_node: i.usage.by_node.map((r: any) => ({ ...r, cost_usd: sum(b.sessions.filter((s) => s.node_id === r.node)), tokens_cache_read: reads(b.sessions.filter((s) => s.node_id === r.node)) })) };
  i.budget_cap = { ...i.budget_cap, cap_usd: 5, spent_usd: 5 };
}

/** An in-place item whose checkout moved to main while its implementer ran (`inplace.check_task`): Kraft switched the
 *  person's checkout onto the item's branch before the task (`checkout_switched`), the session ran to its end, and the
 *  item stopped for the person instead of switching the checkout back. Built on the capped sample's attempt. */
function checkoutMoved(i: any, b: ItemBundle) {
  const s = b.sessions.find((x) => x.status === "capped_out")!;
  noTimeCap(i, b);
  finishedPlan(b);
  Object.assign(s, { status: "done", session_summary_ref: `items/${i.id}/sessions/${s.id}/out/summary.md` });
  const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id)!;
  exit.payload = { ...exit.payload, status: "done" };
  const branch = `kraft/${i.id}`;
  const started = b.events.findIndex((e) => e.type === "worker_session_started" && e.payload.session_id === s.id);
  b.events.splice(started, 0, { ...b.events[started], type: "checkout_switched", payload: { from: "main", to: branch }, node_id: null });
  b.events.forEach((e, n) => { e.seq = n + 1; });
  const task = s.hook_point;
  const reason = `the checkout moved to main during the run of ${task} in ${i.repo}. Kraft did not switch it back: retry the item once ${i.repo} may go back to ${branch}`;
  const facts = { task, rerun: false, session: s.id, round: s.round };
  const last = [...b.events].reverse().find((e) => e.type === "work_item_needs_human")!;
  last.payload = { node_id: last.payload.node_id, reason, kind: "checkout", facts, session_id: s.id };
  i.stop = { ...i.stop, kind: "checkout", reason, facts };
  Object.assign(i, { in_place: true, worktree_path: i.repo });
}

/** The running row, in kraft-vscode's own checkout: what `waitsForCheckout` waits behind. */
function runsInPlace(i: any) {
  // On verification's code review: no plan progress, which the board sends only while the item runs its implementation
  // node (`progress.for_item`).
  i.progress = null;
  i.step = { index: 2, count: 2, name: "review", task: "code_review" };
  Object.assign(i, { in_place: true, worktree_path: i.repo });
}

/** An in-place item resumed while the running row (`runsInPlace`, ROWS[6], by its seed) uses the same checkout:
 *  `store.checkout_reason` on its queued event and its `queued`. */
function waitsForCheckout(i: any, b: ItemBundle) {
  const held = { why: "checkout", held_by: hex(1306), held_by_title: "Bump the VS Code extension to schema v4" };
  Object.assign(b.events.find((e) => e.type === "work_item_queued")!.payload, held);
  Object.assign(i, { in_place: true, worktree_path: i.repo, queued: { ...i.queued, ...held } });
}

const ROWS: Row[] = [
  { sc: "needs-gate", title: "Design the caching layer for document search", repo: "kraft-plugins", bead: "kraft-cb59", age: 4 },
  { sc: "capped", title: "Fix flaky retry test in the worker pool", repo: "kraft-core", bead: "kraft-7d21", age: 22 },
  { sc: "needs-you", title: "Add rate limit headers to the public API", repo: "kraft-api", bead: "kraft-a3f0", age: 60 },
  { sc: "capped", title: "Migrate the config loader to pydantic v2", repo: "kraft-core", bead: "kraft-58e2", age: 120, spend: true, tweak: spendCapped },
  { sc: "failed", title: "Retry on 429 from the forge API", repo: "kraft-api", bead: "kraft-b108", age: 40 },
  { sc: "paused", title: "Trim the review prompts for the docs chain", repo: "kraft-docs", bead: "kraft-4e19", age: 75, chain: "docs_only" },
  { sc: "running", title: "Bump the VS Code extension to schema v4", repo: "kraft-vscode", bead: "kraft-91bc", age: 6, tweak: runsInPlace },
  { sc: "waiting", title: "Document the policy sandbox settings", repo: "kraft-docs", bead: "kraft-e410", age: 14, chain: "docs_only" },
  { sc: "escalated", title: "Lint fan-out for multi-repo chains", repo: "kraft-plugins", bead: "kraft-2c77", age: 31 },
  { sc: "paused", title: "Spike: stream logs over SSE", repo: "kraft-core", bead: "kraft-f5d3", age: 180, fresh: true },
  { sc: "done", title: "Release notes for 0.14", repo: "kraft-docs", bead: "kraft-3a90", age: 120, chain: "docs_only" },
  { sc: "done", title: "Remove the legacy poller flags", repo: "kraft-core", bead: "kraft-1b6e", age: 1440 },
  { sc: "cancelled", title: "Rename the harness profiles", repo: "kraft-core", bead: "kraft-88ca", age: 2880 },
  { sc: "done", title: "Add dark mode to the docs site", repo: "kraft-docs", bead: "kraft-c2d9", age: 4320, chain: "docs_only" },
  { sc: "done", title: "Pin the SDK schema version", repo: "kraft-vscode", bead: "kraft-0e7a", age: 5760 },
  { sc: "done", title: "Split the intake poller from the API process", repo: "kraft-api", bead: "kraft-6fb3", age: 7200 },
  { sc: "done", title: "Fix off-by-one in log tail", repo: "kraft-core", bead: "kraft-41d8", age: 8640 },
  // Last, so every row above keeps its seed and so its id.
  { sc: "queued", title: "Index the review threads for search", repo: "kraft-api", bead: "kraft-7c40", age: 3 },
  { sc: "blocked", title: "Tail worker logs in the console", repo: "kraft-core", bead: "kraft-b2e7", age: 9 },
  { sc: "capped", title: "Cache the search index in place", repo: "kraft-plugins", bead: "kraft-e3c1", age: 17, tweak: checkoutMoved },
  { sc: "queued", title: "Tidy the extension's settings page", repo: "kraft-vscode", bead: "kraft-6a2f", age: 2, tweak: waitsForCheckout },
];

const ARCHIVED: Row[] = [
  { sc: "archived", title: "Trim the default chain prompts", repo: "kraft-plugins", bead: "kraft-9a02", age: 11520 },
  { sc: "archived", title: "Document kraft admin reload", repo: "kraft-docs", bead: "kraft-d7e4", age: 12960, chain: "docs_only" },
  { sc: "archived", title: "Drop the v0 webhook payloads", repo: "kraft-api", bead: "kraft-5c31", age: 20160, tweak: (i) => { i.status = "abandoned"; } },
];

const LONG_TAIL = " so the board has to ellipsize a title that runs far past the row's width without pushing the ticks or the action off it";

const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/;
/** Every moment in `o` moved by `by(ms)`, but a retry or a resume still to come: what the item waits for stays put. */
function shift(o: any, by: (ms: number) => number, now: number, key = ""): any {
  if (typeof o === "string") return ISO.test(o) && !(["retry_at", "resume_at"].includes(key) && Date.parse(o) > now) ? new Date(by(Date.parse(o))).toISOString() : o;
  if (o && typeof o === "object") for (const k of Object.keys(o)) o[k] = shift(o[k], by, now, Array.isArray(o) ? key : k);
  return o;
}
/** The item's history retimed: a person took `wait` minutes longer over each gate they approved (nothing runs while a
 *  gate waits), and then all of it moved so its last event is at `end`. */
function retime(b: ItemBundle, wait: number, end: number) {
  const now = Date.parse(NG_NOW);
  const approved = b.events.filter((e) => e.type === "gate_approved" && e.payload.by === "human").map((e) => Date.parse(e.created_at));
  const longer = (ms: number) => ms + approved.filter((a) => a <= ms).length * wait * 60_000;
  const last = longer(Date.parse(b.events.at(-1)!.created_at));
  shift(b, (ms) => longer(ms) + end - last, now);
}

function build(rows: Row[], variant: Variant, seed0: number, bundles: Record<string, ItemBundle>): string[] {
  const long = variant === "long";
  return rows.map((r, n) => {
    // Its own brief, files and merge request, in the default sample (ngContent.ts).
    const content = variant === "default" ? CONTENT[r.bead] : undefined;
    const b = r.fresh ? buildNeverStarted(seed0 + n, long ? "long" : "default", r.chain) : buildNgItem(r.sc, seed0 + n, long ? "long" : "default", r.chain, content && mrOf(r.repo, content.mr), content, { spend: r.spend });
    const i = b.item;
    i.id = hex(seed0 + n);
    i.title = long ? r.title + LONG_TAIL : r.title;
    i.repo = (long ? LONG_R : R)(r.repo);
    i.bead_id = r.bead;
    if (content) b.content = content;
    r.tweak?.(i, b);
    // Its history ends when it was last touched, so each item has run for its own time: from its first node to the
    // clock, or to its last event once it has ended.
    const ran = content && b.events.length > 0;
    if (ran) retime(b, (n * 7) % 19, Date.parse(t(130 - r.age)));
    i.updated_at = t(130 - r.age);
    i.created_at = ran ? b.events[0].created_at : t(130 - r.age - 90);
    if (i.archived_at) i.archived_at = t(130 - r.age + 30);
    if (ran) {
      i.started_at = b.events.find((e) => e.type === "node_started")?.created_at ?? null;
      if (["completed", "abandoned"].includes(i.status)) i.ended_at = b.events.at(-1)!.created_at;
    }
    if (content) i.description = content.description;
    for (const s of b.sessions) s.work_item_id = i.id;
    // Its own repo and title where an event names one (`work_item_created`, `branch_pushed`).
    for (const e of b.events) { e.work_item_id = i.id; if (e.payload.repo) e.payload.repo = i.repo; if (e.type === "work_item_created") e.payload.title = i.title; }
    if (r.fresh) b.events.push({ seq: 1, work_item_id: i.id, type: "work_item_created", payload: { title: i.title, repo: i.repo, chain_template: i.chain_template }, node_id: null, created_at: i.created_at });
    bundles[i.id] = b;
    return i.id;
  });
}

/** The ids of the list GET /work-items answers for an ng-board case, and of the archived list, in order. */
export function buildNgBoard(variant: Variant, bundles: Record<string, ItemBundle>): { list: string[]; archived: string[] } {
  if (variant === "empty") return { list: [], archived: [] };
  const list = build(ROWS, variant, 1300, bundles);
  if (variant === "many")
    for (let k = 1; k < 4; k++) list.push(...build(ROWS.map((r) => ({ ...r, title: `${r.title} (${k + 1})`, bead: `${r.bead}${k}`, age: r.age + k * 7 })), "default", 1300 + k * 50, bundles));
  return { list, archived: build(ARCHIVED, variant, 1500, bundles) };
}

/** The composer's chains (ux2-W6 F), as the server resolves them: the items' own (ngChain.ts), each node with the
 *  attachment that covers it (`covered_by`, as recorded). The composer, the phone and the dry run all read this list. */
export const NG_CHAINS = ["default", "quick-task", "docs_only"].map((id) => {
  const c = chainOf(id)!;
  return { id, nodes: c.nodes, gates: c.nodes.filter((n) => n.kind === "gate").length };
});
/** What ngDryRun reads of a node. Every recorded node has all four (`fix_loop` is null without a loop), so a
 *  re-recording that drops one fails tsc. */
type DryRunNode = { id: string; kind: string; covered_by: string | null; fix_loop: unknown };
/** `team-default`, a chain that extends `default`: GET /templates/chains's entry for it, a real server's answer
 *  (draftViews.json). Every chain list lists it, so the composer can pick it and its dry run reads its own nodes. */
export const NG_EXTENDING_CHAIN = { ...draftViews.extending, nodes: draftViews.extending.nodes satisfies DryRunNode[] as DryRunNode[] };
export const NG_REPOS = ["kraft-plugins", "kraft-core", "kraft-api", "kraft-vscode", "kraft-docs", "kraft-lite", "vendor-schemas"].map((name, i) => ({
  ...repo(R(name), i), id: name, enabled: true, default_chain: name === "kraft-docs" ? "docs_only" : "default",
  // Two repos name shared-gl (repo 0 does in `repo()`): the Forge accounts table's "Used by" cell holds a list, not one name.
  ...(name === "kraft-api" && { forge_account: "shared-gl" }),
}));
/** kraft-plugins roots a workspace with two members: the draft page's cross-repo picker (G.5). */
export const NG_WORKSPACES = {
  "plugins-ws": { id: "plugins-ws", root: "kraft-plugins", root_pointer_default: "ignore", members: {
    "kraft-lite": { repository: "kraft-lite", path: "plugins/kraft-lite" },
    schemas: { repository: "vendor-schemas", path: "vendor/schemas" },
  } },
};

/** B33's dry run over NG_CHAINS: what attachments cover and what skip_nodes drop. */
export function ngDryRun(body: { chain_template?: string; attachments?: { kind: string; path: string }[]; skip_nodes?: string[] }) {
  const missing = (body.attachments ?? []).find((a) => a.path.includes("missing"));
  if (missing) return { status: 422, body: { detail: `attachment not found: ${missing.path}` } };
  const chain = body.chain_template ?? "default";
  const tpl = [...NG_CHAINS, NG_EXTENDING_CHAIN].find((c) => c.id === chain);
  // `deps.resolve_chain_or_422`'s answer for a chain the library cannot resolve, in the resolver's words (`library.resolve_chain`).
  const known = [...NG_CHAINS, NG_EXTENDING_CHAIN].map((c) => `'${c.id}'`).join(", ");
  if (!tpl) return { status: 422, body: { detail: `chain '${chain}': no chain '${chain}' in this library; known chains: [${known}]` } };
  const kinds = new Set((body.attachments ?? []).map((a) => a.kind));
  const skipped = (tpl.nodes as DryRunNode[]).flatMap((n) =>
    n.covered_by && kinds.has(n.covered_by) ? [{ node: n.id, why: "covered_by", kind: n.covered_by }] : (body.skip_nodes ?? []).includes(n.id) ? [{ node: n.id, why: "skip" }] : []);
  const nodes = (tpl.nodes as DryRunNode[]).filter((n) => !skipped.some((s) => s.node === n.id));
  // A fix loop's cap: its frozen `max_attempts`, and the policy's default wall clock (`executor.fix_loop_cap`). A chain
  // the mock has no frozen chain for (team-default extends default) reads default's.
  const loops = Object.fromEntries((chainOf(chain) ?? chainOf("default")!).frozen.nodes.filter((n: any) => n.fix_loop).map((n: any) => [n.id, n.fix_loop.max_attempts]));
  return { status: 200, body: {
    dry_run: true, nodes, skipped, gates: nodes.filter((n) => n.kind === "gate").map((n) => n.id),
    // The demo team's policy caps: `work_item_usd` raised from the shipped 10 to 20, so a request for changes can rerun an item that already spent most of 10 (`daily_usd: 50` as shipped).
    caps: { budget_usd: 20, budget_source: "policy", daily_usd: 50, nodes: Object.fromEntries(nodes.filter((n) => n.fix_loop).map((n) => [n.id, { attempts: loops[n.id] ?? 3, wall_clock_s: 3600 }])) },
  } };
}
