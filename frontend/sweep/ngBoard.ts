import { hex, t, type ItemBundle, type Variant } from "./fixtures";
import { buildNgItem, type NgScenario } from "./ngItems";

/**
 * The /ng board's list (ux2-W6): one row per kind of row AreaBoard.dc.html
 * draws, each built on ngItems.ts's chain so the peek has a full detail. Served
 * by GET /work-items only when a case asks (`MockOptions.ngBoard`), so no
 * shipped cell and no ng-item cell changes. Ages count back from NG_NOW
 * (T0 + 130 minutes); the list's `stop` carries no `facts`, as W4's list sends it.
 */

type Row = { sc: NgScenario; title: string; repo: string; bead: string; age: number; chain?: string; tweak?: (i: any) => void };

const R = (name: string) => `/Users/dev/code/${name}`;
const LONG_R = (name: string) => `/Volumes/Work/clients/acme-corporation/monorepo/services/${name}-with-a-long-checkout-path`;

const ROWS: Row[] = [
  { sc: "needs-gate", title: "Design the caching layer for document search", repo: "kraft-plugins", bead: "kraft-cb59", age: 4 },
  { sc: "capped", title: "Fix flaky retry test in the worker pool", repo: "kraft-core", bead: "kraft-7d21", age: 22 },
  { sc: "needs-you", title: "Add rate limit headers to the public API", repo: "kraft-api", bead: "kraft-a3f0", age: 60 },
  { sc: "capped", title: "Migrate the config loader to pydantic v2", repo: "kraft-core", bead: "kraft-58e2", age: 120, tweak: (i) => {
    i.stop = { ...i.stop, kind: "budget", reason: "Spend cap reached · $5.00 of $5.00" };
  } },
  { sc: "failed", title: "Retry on 429 from the forge API", repo: "kraft-api", bead: "kraft-b108", age: 40 },
  { sc: "paused", title: "Trim the review prompts for the docs chain", repo: "kraft-docs", bead: "kraft-4e19", age: 75, chain: "docs_only" },
  { sc: "running", title: "Bump the VS Code extension to schema v4", repo: "kraft-vscode", bead: "kraft-91bc", age: 6, tweak: (i) => {
    i.progress = { current: 2, total: 3, title: "Thread findings into the prompt" };
  } },
  { sc: "waiting", title: "Document the policy sandbox settings", repo: "kraft-docs", bead: "kraft-e410", age: 14, chain: "docs_only" },
  { sc: "escalated", title: "Lint fan-out for multi-repo chains", repo: "kraft-plugins", bead: "kraft-2c77", age: 31 },
  { sc: "running", title: "Spike: stream logs over SSE", repo: "kraft-core", bead: "kraft-f5d3", age: 180, tweak: (i) => {
    i.status = "paused"; i.display_status = "paused"; i.current_node_id = null; i.stop = null;
  } },
  { sc: "done", title: "Release notes for 0.14", repo: "kraft-docs", bead: "kraft-3a90", age: 120, chain: "docs_only" },
  { sc: "done", title: "Remove the legacy poller flags", repo: "kraft-core", bead: "kraft-1b6e", age: 1440 },
  { sc: "cancelled", title: "Rename the harness profiles", repo: "kraft-core", bead: "kraft-88ca", age: 2880 },
  { sc: "done", title: "Add dark mode to the docs site", repo: "kraft-docs", bead: "kraft-c2d9", age: 4320, chain: "docs_only" },
  { sc: "done", title: "Pin the SDK schema version", repo: "kraft-vscode", bead: "kraft-0e7a", age: 5760 },
  { sc: "done", title: "Split the intake poller from the API process", repo: "kraft-api", bead: "kraft-6fb3", age: 7200 },
  { sc: "done", title: "Fix off-by-one in log tail", repo: "kraft-core", bead: "kraft-41d8", age: 8640 },
];

const ARCHIVED: Row[] = [
  { sc: "archived", title: "Trim the default chain prompts", repo: "kraft-plugins", bead: "kraft-9a02", age: 11520 },
  { sc: "archived", title: "Document kraft admin reload", repo: "kraft-docs", bead: "kraft-d7e4", age: 12960, chain: "docs_only" },
  { sc: "archived", title: "Drop the v0 webhook payloads", repo: "kraft-api", bead: "kraft-5c31", age: 20160, tweak: (i) => { i.status = "abandoned"; } },
];

const LONG_TAIL = " so the board has to ellipsize a title that runs far past the row's width without pushing the ticks or the action off it";

function build(rows: Row[], variant: Variant, seed0: number, bundles: Record<string, ItemBundle>): any[] {
  const long = variant === "long";
  return rows.map((r, n) => {
    const b = buildNgItem(r.sc, seed0 + n, long ? "long" : "default");
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
    r.tweak?.(i);
    for (const s of b.sessions) s.work_item_id = i.id;
    for (const e of b.events) e.work_item_id = i.id;
    bundles[i.id] = b;
    // The list's row: W4 sends `stop` without facts, task or attempt.
    return { ...i, stop: i.stop ? { kind: i.stop.kind, node: i.stop.node, resume_at: i.stop.resume_at, reason: i.stop.reason } : null };
  });
}

/** The list GET /work-items answers for an ng-board case, and the archived list. */
export function buildNgBoard(variant: Variant, bundles: Record<string, ItemBundle>): { list: any[]; archived: any[] } {
  if (variant === "empty") return { list: [], archived: [] };
  const list = build(ROWS, variant, 1300, bundles);
  if (variant === "many")
    for (let k = 1; k < 4; k++) list.push(...build(ROWS.map((r) => ({ ...r, title: `${r.title} (${k + 1})`, bead: `${r.bead}${k}`, age: r.age + k * 7 })), "default", 1300 + k * 50, bundles));
  return { list, archived: build(ARCHIVED, variant, 1500, bundles) };
}
