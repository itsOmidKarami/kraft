import { describe, expect, it } from "vitest";
import type { DisplayStatus, StopKind, WorkItemStop } from "../../types";
import { steerDoor } from "../phone/item/Composer";
import { kebabOf, pairOf, type ActId } from "../phone/item/model";
import { nodeBar, type NodeActId } from "../phone/node/model";
import { chainGraph } from "./graph";
import { footerState } from "./nodeGraph";
import { footerActs } from "./panes/PathFooter";
import { archivable, budgetRaise, headerState, MAIN_LABEL, menuDoors, type Main, type PanelItem } from "./status";
import { detail } from "./testkit";
import type { ItemDetail } from "./useItem";

type Scope = WorkItemStop["scope"];
/** A budget stop is on the item's own cap unless a row says otherwise: the one `/budget/raise` takes. */
const at = (display_status: DisplayStatus, kind?: StopKind, scope: Scope = "work_item") =>
  headerState({ display_status, current_node_id: "n", stop: kind ? { kind, node: "n", resume_at: null, reason: null, ...(kind === "budget" && { scope }) } : null });
const FULL = ["escalate", "complete", "archive", "cancel"];
// /escalate refuses a running or waiting item, and one whose escalation turn already runs.
const NO_ESC = ["complete", "archive", "cancel"];

describe("headerState: every display status, from the server's fields only", () => {
  it.each([
    ["running", undefined, "RUNNING", "neutral", "pause", NO_ESC],
    ["waiting", "rate_limit", "WAITING", "info", "pause", NO_ESC],
    // R11b-01: /pause answers every stopped item 409, so a needs-you stop's main is its way on, as the phone's bar has it.
    ["needs_you", "gate", "NEEDS YOU", "warn", "gate", FULL],
    ["needs_you", "question", "NEEDS YOU", "warn", "answer", FULL],
    ["needs_you", "conflict", "NEEDS YOU", "warn", "conflicts", FULL],
    ["needs_you", "mr_closed", "NEEDS YOU", "warn", "reopen", FULL],
    ["needs_you", "stuck", "NEEDS YOU", "warn", "retry", FULL],
    ["needs_you", "cap", "NEEDS YOU", "warn", "raise", FULL],
    ["needs_you", "budget", "NEEDS YOU", "warn", "raise", FULL],
    ["needs_you", "budget", "NEEDS YOU", "warn", "retry", FULL, "daily"],
    ["escalated", undefined, "ESCALATED", "warn", "retry", NO_ESC],
    ["failed", "failed", "FAILED", "bad", "retry", FULL],
    ["paused", undefined, "PAUSED", "muted", "resume", FULL],
    ["done", undefined, "DONE", "ok", "archive", []],
    ["cancelled", undefined, "CANCELLED", "muted", "archive", ["archive"]],
    ["archived", undefined, "ARCHIVED", "muted", "restore", []],
  ] as const)("%s (%s) → %s, %s, %s", (status, kind, badge, tone, main, panel, scope?: Scope) => {
    expect(at(status, kind, scope)).toEqual({ badge, tone, main, panel });
  });

  it("a paused item that never started is NOT STARTED with Start, not PAUSED with Resume", () => {
    expect(headerState({ display_status: "paused", current_node_id: null, stop: null })).toEqual({ badge: "NOT STARTED", tone: "muted", main: "start", panel: ["cancel"] });
    expect(MAIN_LABEL.start).toBe("Start");
  });

  it("Archive in the panel is live only for done and cancelled", () => {
    expect((["done", "cancelled"] as const).every(archivable)).toBe(true);
    expect((["running", "paused", "failed", "needs_you", "archived"] as const).some(archivable)).toBe(false);
  });
});

describe("budgetRaise: only a budget stop the server would raise offers a raise", () => {
  const stop = (kind: StopKind, over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "n", resume_at: null, reason: null, ...over });
  const POLICY_USD = { limit: { path: "", key: "budget_usd", value: 0.01, maximum: null } } as const;
  it.each<[string, WorkItemStop, "limit" | "item" | null]>([
    ["an item-wide policy budget_usd, by its limit", stop("budget", { ...POLICY_USD, scope: "usd" }), "limit"],
    ["the item's own cap", stop("budget", { scope: "work_item" }), "item"],
    // The item at its own cap too: /budget/raise still refuses, the node's cap stopped it.
    ["a node's budget_usd, the item also at its own cap", stop("budget", { scope: "usd" }), null],
    ["the daily cap", stop("budget", { scope: "daily" }), null],
    ["a token cap", stop("budget", { scope: "tokens" }), null],
    ["a stop event that names no cap", stop("budget"), null],
    ["a cap stop that names a limit", stop("cap", { limit: { path: "", key: "time_cap_minutes", value: 60, maximum: null } }), null],
  ])("%s", (_name, s, want) => {
    // Every item here has spent its own cap: the spend says nothing about which cap stopped it.
    const item = { stop: s, budget_cap: { cap_usd: 10, source: "item" as const, spent_usd: 10 } };
    expect(budgetRaise(item)).toBe(want);
  });
});

/** What the server's lifecycle routes take (`src/kraft/api/routes/lifecycle.py`), one row per
 *  door: the row an item has, and whether that door answers it, not 409. Read off each route's guard. */
type Server = { status: "active" | "waiting" | "rate_limited" | "paused" | "needs_human" | "completed" | "abandoned"; started: boolean; escalation: boolean; question: boolean; gate: boolean; stop?: string; archived?: boolean; walk?: boolean };
type Door = "pause" | "resume" | "retry" | "skip" | "escalate" | "end" | "archive" | "restore" | "reopen-mr" | "raise" | "decide" | "navigate";
const TAKES: Record<Door, (r: Server) => boolean> = {
  pause: (r) => ["active", "waiting", "rate_limited"].includes(r.status),
  // resume and steer: a paused item, or a needs_context stop; never while an escalation turn runs.
  resume: (r) => !r.escalation && (r.status === "paused" || (r.status === "needs_human" && r.question)),
  // A human's retry outranks a live escalation turn (`_retry`'s `_stop_live_sessions`).
  retry: (r) => r.status === "needs_human",
  // A stopped item with a walk still live (a gate's auto-review) answers "a walk is already running".
  skip: (r) => r.started && ["active", "waiting", "paused", "needs_human"].includes(r.status) && !r.escalation && !(r.status === "needs_human" && r.walk),
  escalate: (r) => ["needs_human", "paused"].includes(r.status) && r.started && !r.escalation,
  end: (r) => !["completed", "abandoned"].includes(r.status),
  archive: (r) => ["completed", "abandoned"].includes(r.status) && !r.archived,
  restore: (r) => !!r.archived,
  "reopen-mr": (r) => r.stop === "mr_closed",
  // /budget/raise and a raised policy's retry both claim only a stopped item.
  raise: (r) => r.status === "needs_human",
  decide: (r) => r.gate,
  navigate: () => true,
};

const S = (kind: StopKind, more: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "verification", task: "verification.review.code_review", resume_at: null, reason: "r", ...more });
const session = (status: string) => [{ id: "s1", node_id: "verification", hook_point: "verification.review.code_review", status, attempt: 1, round: 0, created_at: "t", started_at: "t" }] as never;
const base: Server = { status: "active", started: true, escalation: false, question: false, gate: false };
/** Every display status and stop kind the server sends, each with the row behind it. */
const CASES: [string, Partial<ItemDetail>, Partial<Server>][] = [
  ["running", { display_status: "running", status: "active", worker_sessions: session("running") }, {}],
  ["waiting on CI", { display_status: "waiting", status: "waiting", stop: S("wait"), worker_sessions: session("running") }, { status: "waiting" }],
  ["rate limited", { display_status: "waiting", status: "rate_limited", stop: S("rate_limit"), worker_sessions: session("failed") }, { status: "rate_limited" }],
  ["paused", { display_status: "paused", status: "paused", worker_sessions: session("paused") }, { status: "paused" }],
  ["not started", { display_status: "paused", status: "paused", current_node_id: null }, { status: "paused", started: false }],
  ["gate", { display_status: "needs_you", status: "needs_human", stop: S("gate", { node: "plan_approval" }), pending_gate: "plan_approval", current_node_id: "plan_approval" }, { status: "needs_human", gate: true }],
  // An agent reviews the gate before you (auto_escalate): its task's pane footer saw a live session and offered Pause.
  ["gate under an agent's review", { display_status: "needs_you", status: "needs_human", stop: S("gate", { node: "plan_approval" }), pending_gate: "plan_approval", current_node_id: "plan_approval", worker_sessions: session("running") }, { status: "needs_human", gate: true, walk: true }],
  ["question", { display_status: "needs_you", status: "needs_human", stop: S("question"), needs_context_question: "Keep it?", worker_sessions: session("done") }, { status: "needs_human", question: true }],
  ["cap", { display_status: "needs_you", status: "needs_human", stop: S("cap"), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["budget, the item's own cap", { display_status: "needs_you", status: "needs_human", stop: S("budget", { scope: "work_item" }), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["budget, the daily cap", { display_status: "needs_you", status: "needs_human", stop: S("budget", { scope: "daily" }), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["conflict", { display_status: "needs_you", status: "needs_human", stop: S("conflict"), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["mr closed", { display_status: "needs_you", status: "needs_human", stop: S("mr_closed"), worker_sessions: session("done") }, { status: "needs_human", stop: "mr_closed" }],
  ["stuck", { display_status: "needs_you", status: "needs_human", stop: S("stuck"), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["escalated", { display_status: "escalated", status: "needs_human", stop: S("stuck"), worker_sessions: session("failed") }, { status: "needs_human", escalation: true }],
  ["failed", { display_status: "failed", status: "needs_human", stop: S("failed"), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["infra", { display_status: "failed", status: "needs_human", stop: S("infra"), worker_sessions: session("failed") }, { status: "needs_human" }],
  ["done", { display_status: "done", status: "completed", worker_sessions: session("done") }, { status: "completed" }],
  ["cancelled", { display_status: "cancelled", status: "abandoned", worker_sessions: session("cancelled") }, { status: "abandoned" }],
  ["archived", { display_status: "archived", status: "completed", worker_sessions: session("done") }, { status: "completed", archived: true }],
];

const MAIN_DOOR: Record<Main, Door> = { pause: "pause", resume: "resume", start: "resume", raise: "raise", retry: "retry", archive: "archive", restore: "restore", gate: "decide", answer: "resume", conflicts: "navigate", reopen: "reopen-mr" };
const PANEL_DOOR: Record<PanelItem, Door> = { escalate: "escalate", complete: "end", archive: "archive", cancel: "end" };
const PHONE_DOOR = (item: ItemDetail): Record<ActId, Door> => ({
  pause: "pause", steer: steerDoor(item), resume: "resume", start: "resume", reject: "decide", review: "navigate", raise: "raise", retry: "retry", escalate: "escalate", answer: "resume",
  cancel: "end", "reopen-mr": "reopen-mr", conflicts: "navigate", board: "navigate", restore: "restore", settings: "navigate", "open-mr": "navigate", duplicate: "navigate", archive: "archive", complete: "end",
});
const NODE_DOOR: Record<NodeActId, Door> = { pause: "pause", resume: "resume", skip: "skip", "retry-node": "retry", "retry-from": "retry", review: "navigate" };

/** Every door each surface offers an item: the desktop header (main, panel, ⋮), the peek's
 *  footer (the same main), a path's footer, the phone's bar, ⋮ sheet and node bar. */
function offered(item: ItemDetail): [string, Door][] {
  const hs = headerState(item);
  const graph = chainGraph(item, []).nodes;
  const p = pairOf(item);
  const phone = PHONE_DOOR(item);
  return [
    [`header main ${hs.main}`, MAIN_DOOR[hs.main]],
    ...hs.panel.filter((x) => x !== "archive" || archivable(item.display_status)).map((x): [string, Door] => [`header panel ${x}`, PANEL_DOOR[x]]),
    ...menuDoors(item).map((x): [string, Door] => [`header ⋮ ${x}`, x === "duplicate" ? "navigate" : x === "escalate" ? "escalate" : "end"]),
    ...footerActs(item, footerState(item, item.worker_sessions)).map((x): [string, Door] => [`path footer ${x}`, x]),
    ...[p.secondary, p.primary].flatMap((x): [string, Door][] => (x ? [[`phone bar ${x.id}`, phone[x.id]]] : [])),
    ...kebabOf(item).map((x): [string, Door] => [`phone ⋮ ${x.id}`, phone[x.id]]),
    ...item.chain_definition.nodes.flatMap((n) => {
      const b = nodeBar(item, n, graph.find((g) => g.id === n.id)!);
      return [b.secondary, b.primary].flatMap((x): [string, Door][] => (x ? [[`phone node ${n.id} ${x.id}`, NODE_DOOR[x.id]]] : []));
    }),
  ];
}

describe("no surface offers a door the server refuses (R11b-01)", () => {
  it.each(CASES)("%s", (_name, over, row) => {
    const item = detail(over);
    const server = { ...base, ...row };
    const refused = offered(item).filter(([, door]) => !TAKES[door](server)).map(([where, door]) => `${where} → ${door}`);
    expect(refused).toEqual([]);
  });

  // The two layouts agree on the way on from a stop: the header's main and the phone bar's primary.
  // A cap stop is the one they word apart: the phone's raise is its card's link, beside Retry.
  const WAY: Partial<Record<Main | ActId, string>> = { gate: "gate", review: "gate", answer: "answer", raise: "raise", retry: "retry", conflicts: "conflicts", reopen: "reopen-mr", "reopen-mr": "reopen-mr" };
  it.each(CASES.filter(([name, over]) => (over.display_status === "needs_you" || over.display_status === "escalated") && name !== "cap"))("%s: the desktop header and the phone bar offer the same way on", (_name, over) => {
    const item = detail(over);
    expect(WAY[headerState(item).main]).toBe(WAY[pairOf(item).primary!.id]);
  });
});
