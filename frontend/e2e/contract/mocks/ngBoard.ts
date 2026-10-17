import { hex, repo, t, type ItemBundle, type Variant } from "./fixtures";
import { buildNeverStarted, buildNgItem, NG_NODES, type NgScenario } from "./ngItems";

/**
 * The board's list (ux2-W6): one row per kind of row AreaBoard.dc.html
 * draws, each built on ngItems.ts's chain so the peek has a full detail. Served
 * by GET /work-items only when a case asks (`MockOptions.ngBoard`), so no
 * shipped cell and no ng-item cell changes. Ages count back from NG_NOW
 * (T0 + 130 minutes). The list holds ids: GET /work-items reads each row from its bundle
 * (transitions.ts `listRow`, whose `stop` carries no `facts`, as W4's list sends it).
 */

/** `fresh`: filed paused and never started, so it has no history at all (only `work_item_created`), whatever `sc` ran. */
type Row = { sc: NgScenario; title: string; repo: string; bead: string; age: number; chain?: string; fresh?: boolean; tweak?: (i: any, b: ItemBundle) => void };

const R = (name: string) => `/Users/dev/code/${name}`;
const LONG_R = (name: string) => `/Volumes/Work/clients/acme-corporation/monorepo/services/${name}-with-a-long-checkout-path`;

/** The capped sample stopped instead by its $5 spend cap: its stop, its last event and its spend all say so. `board._stop`
 *  names a budget stop's scope; `work_item` is the item's own cap, the one `/budget/raise` takes. */
function spendCapped(i: any, b: ItemBundle) {
  const reason = "Spend cap reached · $5.00 of $5.00";
  i.stop = { ...i.stop, kind: "budget", reason, facts: {}, scope: "work_item" };
  const last = [...b.events].reverse().find((e) => e.type === "work_item_needs_human")!;
  last.payload = { node_id: last.payload.node_id, reason, kind: "budget", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } };
  // Its sessions' costs scaled to the $5 it spent, and its usage read off them again.
  const was = b.sessions.reduce((a, s) => a + (s.cost_usd ?? 0), 0);
  for (const s of b.sessions) s.cost_usd = Math.round(((s.cost_usd ?? 0) * 5 / was) * 10_000) / 10_000;
  const sum = (ss: any[]) => Math.round(ss.reduce((a, s) => a + (s.cost_usd ?? 0), 0) * 10_000) / 10_000;
  i.usage = { ...i.usage, total: { ...i.usage.total, cost_usd: sum(b.sessions) }, by_node: i.usage.by_node.map((r: any) => ({ ...r, cost_usd: sum(b.sessions.filter((s) => s.node_id === r.node)) })) };
  i.budget_cap = { ...i.budget_cap, spent_usd: 5 };
  // A spend cap interrupts nothing: the session that spent the last of it ran to its end (done, its summary written,
  // its usage on its `worker_session_exited` as `store.session_exited` writes it), and the walk stopped before the next
  // agent task launched (`dispatch.over_budget`). No session is capped out: that is a time cap's or a loop's mark.
  for (const s of b.sessions.filter((x) => x.status === "capped_out")) {
    Object.assign(s, { status: "done", session_summary_ref: `.engineering/sessions/${s.id}.md`, tokens_cache_write: 0, tokens_cache_read: 0 });
    const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id)!;
    exit.payload = { session_id: s.id, status: "done", wall_ms: s.wall_ms, model: s.model, tokens_in: s.tokens_in, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: s.tokens_out, cost_usd: s.cost_usd };
  }
}

/** An in-place item whose checkout moved to main while its review ran (`inplace.check_task`): Kraft switched the
 *  person's checkout onto the item's branch before the task (`checkout_switched`), the session ran to its end, and the
 *  item stopped for the person instead of switching the checkout back. Built on the capped sample's attempt. */
function checkoutMoved(i: any, b: ItemBundle) {
  const s = b.sessions.find((x) => x.status === "capped_out")!;
  Object.assign(s, { status: "done", session_summary_ref: `.engineering/sessions/${s.id}.md` });
  const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id)!;
  exit.payload = { ...exit.payload, status: "done" };
  const branch = `kraft/${i.id}`;
  const started = b.events.findIndex((e) => e.type === "worker_session_started" && e.payload.session_id === s.id);
  b.events.splice(started, 0, { ...b.events[started], type: "checkout_switched", payload: { from: "main", to: branch }, node_id: null });
  b.events.forEach((e, n) => { e.seq = n + 1; });
  const task = s.hook_point;
  const reason = `the checkout ${i.repo} moved to main during the run of ${task}. Kraft did not switch it back, nor move those commits: resume once ${i.repo} may go back to ${branch}`;
  const facts = { task, rerun: false, session: s.id, round: s.round };
  const last = [...b.events].reverse().find((e) => e.type === "work_item_needs_human")!;
  last.payload = { node_id: last.payload.node_id, reason, kind: "checkout", facts, session_id: s.id };
  i.stop = { ...i.stop, kind: "checkout", reason, facts };
  Object.assign(i, { in_place: true, worktree_path: i.repo });
}

/** The running row, in kraft-vscode's own checkout: what `waitsForCheckout` waits behind. */
function runsInPlace(i: any) {
  i.progress = { current: 2, total: 3, title: "Thread findings into the prompt" };
  i.step = { index: 2, count: 3, name: "review", task: "code_review" };
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
  { sc: "capped", title: "Migrate the config loader to pydantic v2", repo: "kraft-core", bead: "kraft-58e2", age: 120, tweak: spendCapped },
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

function build(rows: Row[], variant: Variant, seed0: number, bundles: Record<string, ItemBundle>): string[] {
  const long = variant === "long";
  return rows.map((r, n) => {
    const b = r.fresh ? buildNeverStarted(seed0 + n, long ? "long" : "default") : buildNgItem(r.sc, seed0 + n, long ? "long" : "default");
    const i = b.item;
    i.id = hex(seed0 + n);
    i.title = long ? r.title + LONG_TAIL : r.title;
    i.repo = (long ? LONG_R : R)(r.repo);
    i.bead_id = r.bead;
    i.chain_template = r.chain ?? "default";
    i.chain_definition = { ...i.chain_definition, template_id: i.chain_template };
    i.updated_at = t(130 - r.age);
    i.created_at = t(130 - r.age - 90);
    if (i.archived_at) i.archived_at = t(130 - r.age + 30);
    r.tweak?.(i, b);
    for (const s of b.sessions) s.work_item_id = i.id;
    for (const e of b.events) e.work_item_id = i.id;
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

/** The composer's chains (ux2-W6 F), V1 as the server resolves them: spec and
 *  its gate covered by an attached spec, plan and its gate by a plan. */
const COVER: Record<string, string> = { spec: "spec", spec_approval: "spec", plan: "plan", plan_approval: "plan" };
const v1 = (ids: string[]) => NG_NODES.filter((n) => ids.includes(n.id)).map((n) => ({ ...n, covered_by: COVER[n.id] ?? null }));
export const NG_CHAINS = [
  { id: "default", nodes: v1(NG_NODES.map((n) => n.id)), gates: 4 },
  { id: "docs_only", nodes: v1(["spec", "spec_approval", "implementation", "work_brief", "merge_request"]), gates: 1 },
].map((c) => ({ ...c, gates: c.nodes.filter((n) => n.kind === "gate").length }));
export const NG_REPOS = ["kraft-plugins", "kraft-core", "kraft-api", "kraft-vscode", "kraft-docs", "kraft-lite", "vendor-schemas"].map((name, i) => ({
  ...repo(R(name), i), id: name, enabled: true, default_chain: name === "kraft-docs" ? "docs_only" : "default",
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
  const tpl = NG_CHAINS.find((c) => c.id === body.chain_template) ?? NG_CHAINS[0];
  const kinds = new Set((body.attachments ?? []).map((a) => a.kind));
  const skipped = tpl.nodes.flatMap((n) =>
    n.covered_by && kinds.has(n.covered_by) ? [{ node: n.id, why: "covered_by", kind: n.covered_by }] : (body.skip_nodes ?? []).includes(n.id) ? [{ node: n.id, why: "skip" }] : []);
  const nodes = tpl.nodes.filter((n) => !skipped.some((s) => s.node === n.id));
  return { status: 200, body: {
    dry_run: true, nodes, skipped, gates: nodes.filter((n) => n.kind === "gate").map((n) => n.id),
    caps: { budget_usd: 5, budget_source: "policy", daily_usd: 50, nodes: Object.fromEntries(nodes.filter((n) => n.fix_loop).map((n) => [n.id, { attempts: 3, wall_clock_s: 1800 }])) },
  } };
}
