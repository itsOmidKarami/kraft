import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import type { DisplayStatus, WorkItemStop, WorkerSession } from "../../../types";
import { chainGraph } from "../../item/graph";
import { acceptWrites, detail, FROZEN, holdFetch, LOOPED, pendingRun, SCOPE_PATH, scopeChain, scoped, scopeRun, stubFetch, WORKSPACE, type Call } from "../../item/testkit";
import { Toaster } from "../nav/Toaster";
import { nodeBar } from "./model";
import { NodeRoute } from "./NodeRoute";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/pause", "POST /work-items/w1/resume", "POST /work-items/w1/retry", "POST /work-items/w1/skip");

const stop = (kind: WorkItemStop["kind"], over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "verification", resume_at: null, reason: null, ...over });
const session = (over: Partial<WorkerSession>): WorkerSession => ({ id: "s1", work_item_id: "w1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1, thread: 1, round: 0, created_at: "2026-09-13T09:00:00Z", started_at: "2026-09-13T09:00:00Z", exited_at: null, model: "claude-sonnet-4-5", tokens_in: 100, tokens_out: 50, cost_usd: 0.15, wall_ms: 60000, ...over }) as WorkerSession;
const item = (status: DisplayStatus, s: WorkItemStop | null = null, over = {}) => detail({ display_status: status, stop: s, worker_sessions: [session({})], ...over });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
function mount(it: ReturnType<typeof detail>, path: string, answers: Record<string, [number, unknown]> = {}) {
  const calls = stubFetch({ ...WRITES, "GET /work-items/w1": [200, it], "GET /worker-sessions/s1/log": [200, { lines: [{ n: 1, t: "0:03", src: "agent", text: "loaded review_package", summary: "loaded review_package" }, { n: 2, t: "0:05", src: "tool", text: "grep cache", summary: "grep cache" }] }], ...answers });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/work-items/:id/nodes/:node" element={<><NodeRoute /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
  return calls;
}
const posts = (calls: Call[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
const where = () => screen.getByLabelText("where").textContent;
afterEach(() => vi.unstubAllGlobals());

/** Verification with a fix loop that ran: round 1 found something, one repair, then round 2, where the review ran twice. */
const LOOP_NODES = detail().chain_definition.nodes.map((n) => (n.id === "verification" ? { ...n, fix_loop: "verification.fix_loop" } : n));
const looped = (over = {}) => {
  const at = (m: number) => `2026-09-13T09:${String(m).padStart(2, "0")}:00Z`;
  const run = (id: string, hook: string, round: number, attempt: number, m: number, wall_ms: number) => session({ id, hook_point: hook, round, attempt, status: "done", created_at: at(m), started_at: at(m), wall_ms });
  return item("running", null, {
    materialized_chain: LOOPED,
    chain_definition: { template_id: "default", nodes: LOOP_NODES },
    worker_sessions: [
      run("l1", "verification.checks.lint", 0, 1, 0, 5000),
      run("s1", "verification.review.code_review", 0, 1, 1, 80000),
      run("fx", "verification.fix_loop.main.repair", 1, 1, 3, 120000),
      run("l2", "verification.checks.lint", 1, 2, 5, 5000),
      run("s2", "verification.review.code_review", 1, 2, 6, 40000),
      run("s3", "verification.review.code_review", 1, 3, 9, 19000),
    ],
    ...over,
  });
};

describe("nodeBar (D.6): one pair by node state, never Retry while it runs", () => {
  const bar = (it: ReturnType<typeof detail>, id: string) => {
    const api = it.chain_definition.nodes.find((n) => n.id === id)!;
    const g = chainGraph(it, [], Date.parse("2026-09-13T10:00:00Z")).nodes.find((n) => n.id === id)!;
    const b = nodeBar(it, api, g);
    return [b.secondary?.id ?? null, b.primary?.id ?? null];
  };
  it.each([
    ["running node", item("running"), "verification", ["pause", "skip"]],
    // R11b-01: /pause and /skip refuse a live escalation; a human's Retry outranks its turn.
    ["escalated node", item("escalated"), "verification", [null, "retry-node"]],
    // R10b-01: /retry answers 409 to an item that is not stopped, so a paused node resumes and a waiting one pauses.
    ["paused node", item("paused"), "verification", ["skip", "resume"]],
    ["node waiting on CI", item("waiting", stop("wait")), "verification", ["pause", "skip"]],
    // /skip refuses a rate-limited item: Pause, which /pause takes, then Skip or Resume.
    ["rate-limited node", item("waiting", stop("rate_limit"), { status: "rate_limited" }), "verification", [null, "pause"]],
    ["failed node", item("failed", stop("failed")), "verification", ["skip", "retry-node"]],
    ["capped node", item("needs_you", stop("cap")), "verification", ["skip", "retry-node"]],
    ["done node of a stopped item", item("failed", stop("failed")), "plan", [null, "retry-from"]],
    ["done node of a running item", item("running"), "plan", [null, null]],
    ["done node of a paused item", item("paused"), "plan", [null, null]],
    ["node not reached", item("running"), "merge_request", [null, null]],
    ["waiting gate", item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval" }), "plan_approval", [null, "review"]],
    ["a gate not reached", item("running"), "plan_approval", [null, null]],
    ["any node of a done item", item("done"), "plan", [null, null]],
    ["any node of a cancelled item", item("cancelled"), "verification", [null, null]],
  ] as const)("%s", (_n, it, id, want) => expect(bar(it, id)).toEqual(want));

  it("never offers Retry on a running node", () => {
    expect(bar(item("running"), "verification")).not.toContain("retry-node");
    expect(bar(item("running"), "verification")).not.toContain("retry-from");
  });
});

describe("the node screen (D)", () => {
  it("shows the strip, the node's facts, its steps and tasks, and opens a task", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    expect(await screen.findByRole("heading", { level: 1, name: "verification" })).toBeInTheDocument();
    const strip = screen.getByRole("group", { name: "Chain" });
    expect(within(strip).getAllByRole("button").map((b) => b.textContent)).toEqual(["plan", "plan_approval", "verification", "merge_request"]);
    expect(within(strip).getByRole("button", { name: "verification" })).toHaveAttribute("aria-current", "true");
    expect(screen.getAllByText("exec node · running", { exact: false })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: /code_review/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
  });

  it("changes node from the strip with replace, not a new history entry", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    const before = window.history.length;
    await userEvent.click(within(await screen.findByRole("group", { name: "Chain" })).getByRole("button", { name: "merge_request" }));
    await waitFor(() => expect(where()).toBe("/work-items/w1/nodes/merge_request"));
    expect(window.history.length).toBe(before);
  });

  it("keeps the tab in the URL", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("tab", { name: "Config" }));
    expect(where()).toBe("/work-items/w1/nodes/verification?tab=config");
    expect(screen.getByText("default · frozen at intake", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("No item override on this node.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(where()).toBe("/work-items/w1/nodes/verification");
  });

  // R14b-04: an applied draft edits the frozen chain, so `node_overrides` stays empty; the desktop reads the events.
  const drafted = { seq: 2, work_item_id: "w1", type: "chain_revised", node_id: null, payload: { gate: null, source: "draft", changes: [{ op: "override", path: "verification", policy: { budget_usd: 5 } }], diff: [] }, created_at: "2026-09-13T09:00:00Z" };
  it.each([
    ["a node override", { node_overrides: { verification: { attempts: 3, wall_clock_s: 2700 } } }, [], "fix attempts 3 · wall clock 45m · changed for this item"],
    ["a draft applied by Review & apply", {}, [drafted], "verification policy.budget_usd 5 · applied by the draft"],
    ["both", { node_overrides: { verification: { attempts: 3 } } }, [drafted], "fix attempts 3 · changed for this item · verification policy.budget_usd 5 · applied by the draft"],
  ])("shows %s as changed for this item", async (_n, over, events, words) => {
    mount(item("running", null, over), "/work-items/w1/nodes/verification?tab=config", { "GET /work-items/w1/events": [200, events] });
    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(screen.queryByText("No item override on this node.")).toBeNull();
  });

  it.each([
    ["one line", [{ n: 1, t: "0:03", src: "agent", text: "loaded review_package", summary: "loaded review_package" }], "1 line"],
    ["two lines", [{ n: 1, t: "0:03", src: "agent", text: "a", summary: "a" }, { n: 2, t: "0:04", src: "agent", text: "b", summary: "b" }], "2 lines"],
  ])("counts the node's log, %s (R13b-07)", async (_, lines, count) => {
    mount(item("running"), "/work-items/w1/nodes/verification?tab=log", { "GET /worker-sessions/s1/log": [200, { lines }] });
    await screen.findByText(/loaded review_package|\ba\b/);
    expect(document.querySelector(".ph-count")!.textContent).toBe(count);
  });

  it("opens the node for a link to a fix loop's own step, which only the desktop canvas draws", async () => {
    const V = detail().chain_definition.nodes.map((n) => (n.id === "verification" ? { ...n, fix_loop: "verification.fix_loop" } : n));
    // LOOPED's loop is written as tasks, so its one step is `main`.
    mount(item("running", null, { materialized_chain: LOOPED, chain_definition: { template_id: "default", nodes: V } }), "/work-items/w1/nodes/verification?sel=verification.fix_loop.main");
    await screen.findByRole("heading", { level: 1, name: "verification" });
    expect(where()).toBe("/work-items/w1/nodes/verification");
  });

  it("reads a looping node in words: the round of its attempts, the wall clock, and named recovery", async () => {
    const V = detail().chain_definition.nodes.map((n) => (n.id === "verification" ? { ...n, fix_loop: "verification.fix_loop", on_failure: ["verification.repair.repair_pass"] } : n));
    const sessions = [session({ id: "s1", status: "done", round: 1, exited_at: "2026-09-13T09:12:00Z" })];
    mount(item("running", null, { materialized_chain: FROZEN, chain_definition: { template_id: "default", nodes: V }, node_overrides: { verification: { attempts: 3, wall_clock_s: 2700 } }, worker_sessions: sessions }), "/work-items/w1/nodes/verification");
    await screen.findByRole("heading", { level: 1, name: "verification" });
    const facts = document.querySelector(".ph-facts")!;
    const row = (k: string) => within(facts as HTMLElement).getByText(k).nextElementSibling!.textContent;
    // Three fix attempts after the first pass: four rounds, as the desktop counts them (R17b-01).
    expect(row("fix loop")).toBe("round 2 of 4");
    expect(row("wall")).toBe("about 12m of 45m");
    expect(row("on failure")).toBe("repair pass, once");
  });

  it("picks a fix-loop round: its tasks, its repair and its judge, with the round in the URL", async () => {
    mount(looped(), "/work-items/w1/nodes/verification");
    const rounds = await screen.findByRole("group", { name: "Fix loop rounds" });
    expect(within(rounds).getAllByRole("button").map((b) => b.textContent)).toEqual(["round 1 · sent to the fix loop", "round 2 · running"]);
    const task = (name: string) => screen.getByText(name).closest("button")!;
    expect(task("code_review")).toHaveTextContent("19s");
    expect(task("repair")).toHaveTextContent("not yet");
    await userEvent.click(within(rounds).getByRole("button", { name: /round 1/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?round=1");
    expect(task("code_review")).toHaveTextContent("1m");
    expect(task("repair")).toHaveTextContent("done · 2m");
    expect(task("judge")).toHaveTextContent("skipped · first repair");
    // The repair opens as a task, and keeps the round it was opened from.
    await userEvent.click(task("repair"));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.fix_loop.main.repair&round=1");
    expect(await screen.findByRole("heading", { level: 1, name: "repair" })).toBeInTheDocument();
    expect(screen.getByText(/fix-loop repair · between rounds 1 and 2 · done/)).toBeInTheDocument();
  });

  it("drops the round from the URL on the newest, and when the node changes", async () => {
    mount(looped(), "/work-items/w1/nodes/verification?round=1");
    await userEvent.click(await screen.findByRole("button", { name: /round 2/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification");
    await userEvent.click(screen.getByRole("button", { name: /round 1/ }));
    await userEvent.click(within(screen.getByRole("group", { name: "Chain" })).getByRole("button", { name: "plan" }));
    await waitFor(() => expect(where()).toBe("/work-items/w1/nodes/plan"));
  });

  it("reads the log of the round picked", async () => {
    const calls = mount(looped(), "/work-items/w1/nodes/verification?tab=log&round=1");
    await screen.findByText(/loaded review_package/);
    const logs = calls.filter((c) => c.path.endsWith("/log")).map((c) => c.path.split("/")[2]).sort();
    expect(logs).toEqual(["fx", "l1", "s1"]);
  });

  it("has a YAML tab with the node as the item froze it, and what was changed for this item", async () => {
    mount(item("running", null, { materialized_chain: FROZEN, node_overrides: { verification: { attempts: 3 } } }), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("tab", { name: "YAML" }));
    expect(where()).toBe("/work-items/w1/nodes/verification?tab=yaml");
    const text = document.querySelector(".ph-yaml-text")!.textContent!;
    expect(text).toContain("id: verification");
    expect(text).toContain("fix_loop:\n  max_attempts: 2");
    expect(text).toContain("steps:\n  - id: checks\n    tasks:\n      - id: lint");
    expect(text).toContain("attempts: 3  # override");
    expect(text.match(/# override/g)).toHaveLength(1);
    expect(screen.getByText("As frozen at intake, with this item's overrides.")).toBeInTheDocument();
  });

  it("filters the node's log by source and says when nothing is left", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification?tab=log");
    expect(await screen.findByText(/loaded review_package/)).toBeInTheDocument();
    expect(screen.getByText(/code_review · loaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "tool" }));
    expect(screen.queryByText(/loaded review_package/)).toBeNull();
    expect(screen.getByText(/grep cache/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "stdout" }));
    expect(screen.getByText("No lines to show. Clear the filter, or the node has not started.")).toBeInTheDocument();
  });

  it("a gate shows its review path with the reject target, never an invented verdict", async () => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval", worker_sessions: [] }), "/work-items/w1/nodes/plan_approval");
    expect((await screen.findAllByText("gate · waiting for you", { exact: false })).length).toBeGreaterThan(0);
    expect(screen.getByText("waiting for your decision")).toBeInTheDocument();
    expect(screen.getByText("Goes back to plan with your note.")).toBeInTheDocument();
    expect(screen.queryByText("approve")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Review and decide" }));
    expect(where()).toBe("/work-items/w1/review?gate=plan_approval");
  });

  it.each([
    ["shows the gate's message from the frozen chain", "Approve the plan. It marks the MR ready.", true],
    ["has no message row when the gate says nothing", undefined, false],
  ])("PH-14: a gate's Overview %s", async (_, message, shown) => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", ...(message && { message }) }] } });
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval", worker_sessions: [], materialized_chain: frozen }), "/work-items/w1/nodes/plan_approval");
    await screen.findAllByText("gate · waiting for you", { exact: false });
    const keys = [...document.querySelectorAll("dt")].map((d) => d.textContent);
    expect(keys.includes("message")).toBe(shown);
    if (message) expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("a skipped gate says it was skipped, not that it decides when reached (R14b-05)", async () => {
    const skipped = { seq: 3, work_item_id: "w1", type: "node_skipped", node_id: "plan_approval", payload: { node_id: "plan_approval", gate: "plan_approval" }, created_at: "2026-09-13T09:00:00Z" };
    mount(item("done"), "/work-items/w1/nodes/plan_approval", { "GET /work-items/w1/events": [200, [skipped]] });
    expect(await screen.findByText("skipped, no decision needed")).toBeInTheDocument();
    expect(screen.queryByText("decides when the chain reaches it")).toBeNull();
    // R15b-03: the header and the status row say so too, not "approved".
    expect(screen.getAllByText("gate · skipped")).toHaveLength(2);
    expect(screen.queryByText("gate · approved")).toBeNull();
  });

  // R15b-01: the desktop's gate pane shows the last test run on the gate; so does the phone's page, each red scope linked to its log.
  it.each([
    ["green", { passed: true, scopes: [{ command: "just test", scope: "**", passed: true, exit_code: 0, session_id: "s1" }] }, "✓ 1 scope", null],
    ["red", { passed: false, scopes: [{ command: "just test", scope: "web/**", passed: false, exit_code: 1, session_id: "s2" }, { command: "just lint", scope: "api/**", passed: true, exit_code: 0, session_id: "s3" }] }, "✗ 1 of 2 scopes · web/**", "/api/worker-sessions/s2/log"],
  ])("a gate shows its %s tests row", async (_name, test_result, text, log) => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval", worker_sessions: [], test_result }), "/work-items/w1/nodes/plan_approval");
    const row = (await screen.findByText("tests")).closest("div")!;
    expect(row).toHaveTextContent(`tests${text}`);
    if (log) expect(within(row).getByRole("link")).toHaveAttribute("href", log);
  });

  it("a gate with no test run has no tests row, and an exec node never has one", async () => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval", worker_sessions: [] }), "/work-items/w1/nodes/plan_approval");
    await screen.findByText("waiting for your decision");
    expect(screen.queryByText("tests")).toBeNull();
  });

  it("falls back to the item when the node does not exist", async () => {
    mount(item("running"), "/work-items/w1/nodes/nope");
    await waitFor(() => expect(where()).toBe("/work-items/w1"));
  });
});

describe("the strip", () => {
  it("scrolls the current chip into view and unmounts cleanly when the browser's scrollIntoView returns a value", async () => {
    const spy = vi.fn(() => ({ not: "a function" }));
    Element.prototype.scrollIntoView = spy as never;
    mount(item("running"), "/work-items/w1/nodes/verification");
    await screen.findByRole("heading", { level: 1, name: "verification" });
    expect(spy).toHaveBeenCalled();
    document.body.innerHTML = "";
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });
});

describe("the node's actions (D.6)", () => {
  it("Pause asks, then pauses; Skip asks and names the node", async () => {
    const calls = mount(item("running"), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Pause this item?" })).getByRole("button", { name: "Pause now" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/pause"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Skip node" }));
    expect(screen.getByRole("dialog", { name: "Skip verification?" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Skip node" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").at(-1)).toEqual({ method: "POST", path: "/work-items/w1/skip", body: { path: "verification" } }));
  });

  it("a stopped node retries from its first step straight away", async () => {
    const calls = mount(item("failed", stop("failed")), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("button", { name: "Retry node" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "verification" } }]));
  });

  it("a paused node resumes, and offers no Retry the server would refuse", async () => {
    const calls = mount(item("paused"), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/resume"]));
    expect(screen.queryByRole("button", { name: "Retry node" })).toBeNull();
  });

  it("a done node asks before it rewinds", async () => {
    const calls = mount(item("failed", stop("failed")), "/work-items/w1/nodes/plan");
    await userEvent.click(await screen.findByRole("button", { name: "Retry from here" }));
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Retry from plan?" })).getByRole("button", { name: "Rewind and retry" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "plan" } }]));
  });
});

describe("the task screen (E)", () => {
  const TASK = "/work-items/w1/nodes/verification?sel=verification.review.code_review";
  it("shows the task, its facts and its attempts, and the attempt is in the URL", async () => {
    mount(item("running", null, { worker_sessions: [session({ id: "s1", attempt: 1, status: "failed", wall_ms: 80000 }), session({ id: "s2", attempt: 2, status: "running" })] }), TASK);
    expect(await screen.findByRole("heading", { level: 1, name: "code_review" })).toBeInTheDocument();
    expect(screen.getByText(/agent task · running now · attempt 2 of 2/)).toBeInTheDocument();
    expect(screen.getByText("kraft-cb59 › verification › review")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /#1/ }));
    expect(where()).toContain("attempt=1");
    expect(await screen.findByText(/attempt 1 of 2/)).toBeInTheDocument();
    expect(screen.getByText("1m")).toBeInTheDocument();
    // Back on the newest, the pin drops: the screen follows the next attempt.
    await userEvent.click(screen.getByRole("button", { name: /#2/ }));
    expect(where()).not.toContain("attempt=");
  });

  it.each([
    ["the newest round", "", /agent task · round 2 of 3 · done · attempt 2 of 2/, ["#1 · done", "#2 · done"]],
    ["a round picked", "&round=1", /agent task · round 1 of 3 · done$/, null],
  ])("on a fix-loop node shows one round's attempts, numbered inside it: %s", async (_n, query, header, chips) => {
    mount(looped(), TASK + query);
    expect(await screen.findByText(header)).toBeInTheDocument();
    const group = screen.queryByRole("group", { name: "Attempts" });
    expect(group && within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(chips);
  });

  it("says a fix-loop task did not run in the round shown", async () => {
    mount(looped(), "/work-items/w1/nodes/verification?sel=verification.fix_loop.judge&round=1");
    expect(await screen.findByText(/fix-loop judge · after round 1 · skipped · the first repair runs without the judge/)).toBeInTheDocument();
    expect(screen.getByText("Not run in this round.")).toBeInTheDocument();
  });

  describe("a changed-test-scope task", () => {
    const nodes = detail().chain_definition.nodes.map((n) => (n.id === "verification" ? { ...n, tasks: ["verification.checks.lint", SCOPE_PATH], steps: [["verification.checks.lint"], [SCOPE_PATH]] } : n));
    const [api, web, e2e] = [scopeRun(null, "just test-api", 0, "done", { order: 0 }), scopeRun(null, "just test-web", 0, "failed", { order: 1 }), pendingRun(null, "just test-e2e", 0, { order: 2 })];
    // A run the round made of a scope before its newest one: no `scope_runs` entry names it any more.
    const earlier = { ...web[1], id: "old", attempt: 7, created_at: "2026-09-13T10:00:00Z" };
    const it0 = () => scoped([api, web, e2e], { chain_definition: { template_id: "default", nodes }, worker_sessions: [earlier, api[1], web[1]] });
    const TESTS = `/work-items/w1/nodes/verification?sel=${SCOPE_PATH}`;
    const key = (command: string) => `scope=${encodeURIComponent(`:${command}`).replace(/%20/g, "+")}`;

    it("lists its scopes by repository in place of attempts, says how the round went, and keeps an earlier run", async () => {
      mount(it0(), TESTS);
      const scopes = (await screen.findByText("Scopes")).closest("section")!;
      expect(within(scopes).getAllByRole("button").map((b) => b.textContent)).toEqual(["just test-apidone · 24s", "just test-webfailed · 24s", "just test-e2ewaiting", "earlier run 1 · failed"]);
      // One repository is not counted, as it is not named.
      expect(scopes.querySelector("p")).toHaveTextContent(/^1 of 3 scopes passed$/);
      expect(screen.queryByRole("group", { name: "Attempts" })).toBeNull();
      await userEvent.click(within(scopes).getByRole("button", { name: /earlier run 1/ }));
      expect(where()).toContain("attempt=7");
    });

    it("opens a scope on its own screen, with its facts and its log, and Back is the task", async () => {
      const calls = mount(it0(), TESTS);
      await userEvent.click(await screen.findByRole("button", { name: /just test-api/ }));
      expect(where()).toBe(`${TESTS}&${key("just test-api")}`);
      expect(await screen.findByRole("heading", { level: 1, name: "just test-api" })).toBeInTheDocument();
      expect(screen.getByText("test scope · round 1 · passed 24s")).toBeInTheDocument();
      expect(screen.getByText("kraft-cb59 › verification › tests › test_changed_scopes")).toBeInTheDocument();
      const facts = document.querySelector(".ph-facts") as HTMLElement;
      const row = (k: string) => within(facts).getByText(k).nextElementSibling!.textContent;
      expect([row("status"), row("command"), row("paths"), row("execution")]).toEqual(["passed · 24s", "just test-api", "api/**", "sequential"]);
      expect(within(facts).queryByText("repo")).toBeNull();
      expect(screen.getByRole("button", { name: "Task" })).toBeInTheDocument();
      await userEvent.click(screen.getByRole("tab", { name: "Log" }));
      await waitFor(() => expect(calls.some((c) => c.path === `/worker-sessions/${api[1].id}/log`)).toBe(true));
      // The task fact goes back to the task, on the scope's screen no longer.
      await userEvent.click(screen.getByRole("tab", { name: "Overview" }));
      await userEvent.click(screen.getByRole("button", { name: "test_changed_scopes" }));
      await waitFor(() => expect(where()).toBe(TESTS));
    });

    it("names a scope's repository, and counts repositories, when the item has several", async () => {
      const several = scoped([scopeRun("ws", "just test-a", 0, "done"), scopeRun("pkg", "just test-b", 0, "failed")], { chain_definition: { template_id: "default", nodes }, materialized_chain: scopeChain("sequential", WORKSPACE) });
      mount(several, TESTS);
      const scopes = (await screen.findByText("Scopes")).closest("section")!;
      expect(scopes.querySelector("p")).toHaveTextContent("1 of 2 scopes passed · 2 of 3 reached · run in order, stop at the first failure");
      await userEvent.click(within(scopes).getByRole("button", { name: /just test-b/ }));
      expect(await screen.findByText("kraft-cb59 › verification › tests › test_changed_scopes › pkg")).toBeInTheDocument();
      expect(within(document.querySelector(".ph-facts") as HTMLElement).getByText("repo").nextElementSibling).toHaveTextContent("pkg");
    });

    it.each([
      ["one that waits has no log to read", "just test-e2e", "test scope · round 1 · waiting", null],
      ["one the round did not pick says so", "just test-gone", "test scope · round 1 · not picked", "Not picked: no changed path reaches it this round."],
    ])("opens a scope with no run: %s", async (_n, command, sub, body) => {
      mount(it0(), `${TESTS}&${key(command)}`);
      expect(await screen.findByText(sub)).toBeInTheDocument();
      expect(screen.queryByRole("tab", { name: "Log" })).toBeNull();
      if (body) expect(screen.getByText(body)).toBeInTheDocument();
    });
  });

  it("keeps an ordinary task on its own screen when the URL names a scope", async () => {
    mount(item("running"), `${TASK}&scope=%3Ajust+test-api`);
    expect(await screen.findByRole("heading", { level: 1, name: "code_review" })).toBeInTheDocument();
    expect(screen.getByText(/agent task · running now/)).toBeInTheDocument();
  });

  it.each([
    // One repository: nothing to tell apart, so it is not named (as the desktop's frame has it).
    ["one repository is its scopes alone", () => [scopeRun(null, "just test-api", 0, "done")], {}, []],
    ["one repository with nothing run still says why", () => [], {}, ["not reached"]],
    ["several are each named, with how they went", () => [scopeRun("ws", "just test-a", 0, "done"), scopeRun("pkg", "just test-b", 0, "failed")], { materialized_chain: scopeChain("sequential", WORKSPACE) }, ["ws · done · 24s", "pkg · failed · 24s", "web · not reached · pkg failed"]],
  ])("heads a changed-test-scope task's scopes by repository only when there are several: %s", async (_n, runs, over, lines) => {
    const nodes = detail().chain_definition.nodes.map((n) => (n.id === "verification" ? { ...n, tasks: ["verification.checks.lint", SCOPE_PATH], steps: [["verification.checks.lint"], [SCOPE_PATH]] } : n));
    mount(scoped(runs(), { chain_definition: { template_id: "default", nodes }, ...over }), `/work-items/w1/nodes/verification?sel=${SCOPE_PATH}`);
    const scopes = (await screen.findByText("Scopes")).closest("section")!;
    expect([...scopes.querySelectorAll(".ph-scope-repo > p")].map((p) => p.textContent)).toEqual(lines);
  });

  it("has no Thread tab on an ordinary task, and Thread first on the escalation", async () => {
    mount(item("running"), TASK);
    await screen.findByRole("heading", { name: "code_review" });
    expect(screen.queryByRole("tab", { name: "Thread" })).toBeNull();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Log", "Config"]);
  });

  it("the escalation task opens on its Thread", async () => {
    mount(item("running", null, { worker_sessions: [session({ id: "e1", hook_point: "verification.escalation.escalation", status: "done" })] }), "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", {
      "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "escalation_message", payload: { thread: 1, turn: 1, message: "decide if the race is real" }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" }]],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "escalation" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Thread", "Overview", "Log", "Config"]);
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
  });

  it("names the harness the attempt ran on", async () => {
    mount(item("running", null, { worker_sessions: [session({ harness: "codex" })] }), TASK);
    expect(await screen.findByText("harness")).toBeInTheDocument();
    expect(screen.getByText("codex")).toBeInTheDocument();
  });

  it("shows the escalation thread through the thread picked, and all of it on the latest", async () => {
    const esc = (id: string, thread: number, created_at: string) => session({ id, hook_point: "verification.escalation.escalation", status: "done", thread, created_at });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: seq, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    mount(item("running", null, { worker_sessions: [esc("e1", 1, "2026-09-13T09:00:00Z"), esc("e1b", 1, "2026-09-13T09:02:00Z"), esc("e2", 2, "2026-09-13T09:05:00Z")] }), "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", {
      "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1"), msg(2, 1, "check the lock order too", "e1b"), msg(3, 2, "start over on the lock", "e2")]],
    });
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /thread 1/ })[0]);
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
    expect(screen.getByText("check the lock order too")).toBeInTheDocument();
    expect(screen.queryByText("start over on the lock")).toBeNull();
    expect(screen.getByText("1 later message after this thread.")).toBeInTheDocument();
  });

  it("shows a new thread on the latest without a reload, though the item's updated_at stays put", async () => {
    const esc = (id: string, thread: number, created_at: string) => session({ id, hook_point: "verification.escalation.escalation", status: "failed", thread, attempt: thread, created_at });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: 1, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    const one = item("failed", stop("failed"), { worker_sessions: [esc("e1", 1, "2026-09-13T09:00:00Z")] });
    useStore.setState({ eventsByItem: {} });
    mount(one, "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", { "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1")]] });
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
    stubFetch({
      "GET /work-items/w1": [200, { ...one, worker_sessions: [...one.worker_sessions, esc("e2", 2, "2026-09-13T09:05:00Z")] }],
      "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1"), msg(2, 2, "start over on the lock", "e2")]],
    });
    act(() => useStore.getState().applyEvent({ seq: 2, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    expect(screen.getByText(/attempt 2 of 2/)).toBeInTheDocument();
  });

  it("keeps the newest read of the thread when an older one answers late", async () => {
    const esc = (id: string, thread: number) => session({ id, hook_point: "verification.escalation.escalation", status: "failed", thread, attempt: thread });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: 1, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    const one = item("failed", stop("failed"), { worker_sessions: [esc("e1", 1)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, one] };
    useStore.setState({ eventsByItem: {} });
    const reads = holdFetch(/\/work-items\/w1\/events\?after_seq=/, answers);
    render(<MemoryRouter initialEntries={["/work-items/w1/nodes/verification?sel=verification.escalation.escalation"]}><Routes><Route path="/work-items/:id/nodes/:node" element={<NodeRoute />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(reads).toHaveLength(1));
    answers["GET /work-items/w1"] = [200, { ...one, worker_sessions: [esc("e1", 1), esc("e2", 2)] }];
    act(() => useStore.getState().applyEvent({ seq: 2, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));
    await waitFor(() => expect(reads).toHaveLength(2));
    await act(async () => reads[1]([msg(1, 1, "decide if the race is real", "e1"), msg(2, 2, "start over on the lock", "e2")]));
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    await act(async () => reads[0]([msg(1, 1, "decide if the race is real", "e1")]));
    expect(screen.getByText("start over on the lock")).toBeInTheDocument();
  });

  it("lists a new attempt's documents without a reload", async () => {
    const one = item("running", null, { worker_sessions: [session({ id: "s1", attempt: 1, status: "failed" })] });
    useStore.setState({ eventsByItem: {} });
    mount(one, TASK);
    expect(await screen.findByRole("heading", { level: 1, name: "code_review" })).toBeInTheDocument();
    stubFetch({
      "GET /work-items/w1": [200, { ...one, worker_sessions: [session({ id: "s1", attempt: 1, status: "failed" }), session({ id: "s2", attempt: 2, status: "done" })] }],
      "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [{ document_id: "d2", title: "Review notes", path: "a.md", kind: "reviews", worker_session_id: "s2", hook_point: "verification.review.code_review", attempt: 2 }] }],
    });
    act(() => useStore.getState().applyEvent({ seq: 2, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));
    expect(await screen.findByText(/Review notes/)).toBeInTheDocument();
  });

  it("says what a task that has not started waits for", async () => {
    mount(item("running", null, { worker_sessions: [] }), "/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(await screen.findByText(/After step checks finishes\./)).toBeInTheDocument();
  });

  it("reads the log of the attempt and filters it", async () => {
    mount(item("running"), `${TASK}&tab=log`);
    expect(await screen.findByText(/loaded review_package/)).toBeInTheDocument();
    expect(screen.getByText(/following/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "sys" }));
    expect(screen.getByText("No lines to show. Clear the filter, or the task has not started.")).toBeInTheDocument();
  });
});
void [Where];
