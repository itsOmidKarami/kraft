import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkerSession } from "../../types";
import { PausedCard, StateCard } from "./StateCard";
import { acceptWrites, detail, stubFetch, type Call } from "./testkit";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/keep-waiting", "POST /work-items/w1/reassign", "POST /work-items/w1/reopen-mr", "POST /work-items/w1/resume", "POST /work-items/w1/retry");

afterEach(() => vi.unstubAllGlobals());

const stop = (kind: string, over = {}) => ({ kind, node: "merge_request", task: "merge_request.open.open_draft", attempt: 3, resume_at: null, reason: "The forge refused.", facts: {}, ...over }) as never;
const handlers = () => ({ reload: vi.fn(), onCancel: vi.fn(), onEscalate: vi.fn(), onDuplicate: vi.fn(), onOpenNode: vi.fn() });
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
let where = "";
const Where = () => {
  const l = useLocation();
  where = l.pathname + l.search;
  return null;
};
const routed = (ui: ReactElement) => render(<MemoryRouter initialEntries={["/work-items/w1"]}>{ui}<Where /></MemoryRouter>);
const show = (over: Parameters<typeof detail>[0], h = handlers()) => ({ h, ...routed(<StateCard item={detail(over)} {...h} />) });

describe("StateCard", () => {
  it("failed: where, the reason, the facts sent, Retry from the task by its path", async () => {
    const calls = stubFetch(WRITES);
    const { h } = show({ display_status: "failed", stop: stop("failed", { facts: { error: "403 Forbidden", tried: "3 times over 6m" } }), budget_cap: { cap_usd: 5, source: "policy", spent_usd: 2.41 } });
    const card = screen.getByRole("region", { name: "Failed" });
    expect(card).toHaveTextContent("merge_request › open › open_draft · attempt 3");
    expect(card).toHaveTextContent("The forge refused.");
    expect(card).toHaveTextContent("error403 Forbidden");
    expect(card).toHaveTextContent("spent$2.41 of $5.00");
    await userEvent.click(within(card).getByRole("button", { name: "Retry from open_draft" }));
    await waitFor(() => expect(h.reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "merge_request.open.open_draft" } }]);
    await userEvent.click(within(card).getByRole("button", { name: "Escalate…" }));
    expect(h.onEscalate).toHaveBeenCalled();
    await userEvent.click(within(card).getByRole("button", { name: "Open merge_request →" }));
    expect(h.onOpenNode).toHaveBeenCalledWith("merge_request");
  });

  // R11a-01: a stuck loop had no card, so its only Retry was at the foot of the node's pane.
  // R12b-01: a stuck fix loop stops on its judge, which no retry path can name; the card retries the node.
  const judge = { node: "verification", task: "verification.fix_loop.judge" };
  it.each([
    ["stuck", "Stuck", null, {}, "merge_request › open › open_draft", "Retry from open_draft", "merge_request.open.open_draft"],
    ["stuck", "Stuck", null, judge, "verification › fix_loop › judge", "Retry from verification", "verification"],
    ["question", "Needs you", null, {}, "merge_request › open › open_draft", "Retry from open_draft", "merge_request.open.open_draft"],
    ["worker_lost", "Needs you", null, {}, "merge_request › open › open_draft", "Retry from open_draft", "merge_request.open.open_draft"],
  ] as const)("a needs-you %s stop with no card of its own gets one: the reason, Retry by a path the server takes, Escalate…", async (kind, title, question, over, at, label, path) => {
    const calls = stubFetch(WRITES);
    const { h } = show({ display_status: "needs_you", status: "needs_human", needs_context_question: question, stop: stop(kind, { reason: "stuck: 1 finding(s) unchanged across cycle 1", ...over }) });
    const card = screen.getByRole("region", { name: title });
    expect(card).toHaveTextContent(`${at} · attempt 3`);
    expect(card).toHaveTextContent("stuck: 1 finding(s) unchanged across cycle 1");
    await userEvent.click(within(card).getByRole("button", { name: label }));
    await waitFor(() => expect(h.reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path } }]);
    await userEvent.click(within(card).getByRole("button", { name: "Escalate…" }));
    expect(h.onEscalate).toHaveBeenCalled();
  });

  it("an infra failure's Check the repo settings opens the item's repo in Settings › Repos (WI-4)", async () => {
    stubFetch();
    show({ display_status: "failed", stop: stop("infra") });
    await userEvent.click(within(screen.getByRole("region", { name: "Failed" })).getByRole("button", { name: "Check the repo settings" }));
    expect(where).toBe("/settings/repos/%2Fcode%2Fkraft-plugins");
  });

  it("says what a failed run kept: its branch and the files changed on it (WI-4)", async () => {
    stubFetch({ "GET /work-items/w1/diff": [200, { files: [{ path: "a.py", insertions: 2, deletions: 1 }, { path: "b.py", insertions: 1, deletions: 0 }] }] });
    show({ display_status: "failed", stop: stop("failed"), branch: "kraft/design-the-cache-w1", worktree_exists: true });
    const card = screen.getByRole("region", { name: "Failed" });
    await waitFor(() => expect(card).toHaveTextContent("work keptbranch kraft/design-the-cache-w1 · 2 files"));
  });

  // R10b-01: /retry claims only a stopped item, so a waiting one offers no Retry now
  // and no harness switch (each answered 409); the fallback the policy allows is a fact.
  it("waiting on the provider: says Kraft retries by itself, names the allowed fallback, and offers no Retry the server would refuse", () => {
    stubFetch(WRITES);
    const s = stop("rate_limit", { node: "verification", task: "verification.review.code_review", facts: { harness: "claude-code", fallback: ["codex", "gemini"], fallback_allowed: ["codex"] } });
    show({ display_status: "waiting", stop: s, rate_limit: { count: 2, cap: 5 } });
    const card = screen.getByRole("region", { name: "Waiting on the provider" });
    expect(card).toHaveTextContent("2 of 5 used");
    expect(card).toHaveTextContent("codex is allowed by the harness list");
    expect(card).toHaveTextContent("Kraft retries by itself");
    expect(within(card).queryByRole("button", { name: /Retry|for this attempt/ })).toBeNull();
  });

  it("waiting on CI offers no Retry now: the server would refuse it", () => {
    show({ display_status: "waiting", stop: stop("wait", { reason: "waiting for CI" }) });
    expect(within(screen.getByRole("region", { name: "Waiting on CI" })).queryByRole("button", { name: /Retry/ })).toBeNull();
  });

  it("worker lost: rendered only with the B5 fields, and never for another kind (R2)", async () => {
    const calls = stubFetch(WRITES);
    const facts = { worker: "ci-runner-3", last_seen_at: "2026-09-13T10:08:00Z", reassign_at: "2026-09-13T10:13:00Z", workers_online: ["ci-runner-1"] };
    const { unmount } = show({ display_status: "waiting", stop: stop("worker_lost", { facts: { worker: "ci-runner-3" } }) });
    expect(screen.queryByRole("region")).toBeNull();
    unmount();
    const other = show({ display_status: "waiting", stop: stop("stuck", { facts }) });
    expect(screen.queryByRole("region")).toBeNull();
    other.unmount();
    show({ display_status: "waiting", stop: stop("worker_lost", { facts }) });
    const card = screen.getByRole("region", { name: "Worker lost" });
    await userEvent.click(within(card).getByRole("button", { name: "Reassign now" }));
    await userEvent.click(within(card).getByRole("button", { name: "Keep waiting" }));
    await waitFor(() => expect(posts(calls).map((c) => c.path)).toEqual(["/work-items/w1/reassign", "/work-items/w1/keep-waiting"]));
  });

  it("conflict: the files the handler left and cleared, review and send back", async () => {
    const { h } = show({ display_status: "needs_you", stop: stop("conflict", { facts: { unresolved: ["search/cache.py", "search/reindex.py"], resolved: ["search/config.py"] } }) });
    const card = screen.getByRole("region", { name: "Rebase needs you" });
    expect(card).toHaveTextContent("unresolvedsearch/cache.py · search/reindex.py");
    expect(card).toHaveTextContent("resolvedsearch/config.py");
    await userEvent.click(within(card).getByRole("button", { name: "Send back with guidance" }));
    expect(h.onEscalate).toHaveBeenCalled();
    // Decisions §14: the review page on that node (W8).
    await userEvent.click(within(card).getByRole("button", { name: "Review the conflicts" }));
    expect(where).toBe("/work-items/w1/review?nodes=merge_request");
  });

  it("MR closed: who closed it, Reopen, a new MR from the node that opened it, and Cancel item…", async () => {
    const calls = stubFetch({ ...WRITES, "GET /work-items/w1/events": [200, [
      { seq: 1, work_item_id: "w1", type: "mr_opened", payload: { number: 142 }, node_id: "merge_request", created_at: "2026-09-13T09:00:00Z" },
      { seq: 2, work_item_id: "w1", type: "mr_closed", payload: { ref: 142, by: "mara" }, node_id: null, created_at: "2026-09-13T09:30:00Z" },
    ]] });
    const { h } = show({ display_status: "needs_you", stop: stop("mr_closed", { node: "mr_checks", task: null, facts: { ref: 142, url: "u" } }) });
    const card = screen.getByRole("region", { name: "MR !142 was closed on the forge" });
    await within(card).findByText(/closed by mara/);
    await userEvent.click(within(card).getByRole("button", { name: "Reopen !142" }));
    await userEvent.click(within(card).getByRole("button", { name: "Open a new MR" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)).toEqual([
      { method: "POST", path: "/work-items/w1/reopen-mr", body: {} },
      { method: "POST", path: "/work-items/w1/retry", body: { path: "merge_request" } },
    ]);
    await userEvent.click(within(card).getByRole("button", { name: "Cancel item…" }));
    expect(h.onCancel).toHaveBeenCalled();
  });

  it("cancelled: the reason from the cancel event, Duplicate and Archive", async () => {
    stubFetch({ "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "work_item_cancelled", payload: { reason: "superseded", node_id: "verification" }, node_id: "verification", created_at: "t" }]] });
    const { h } = show({ display_status: "cancelled", stop: null });
    const card = screen.getByRole("region", { name: "Cancelled" });
    expect(await within(card).findByText("superseded")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Duplicate as new item" }));
    expect(h.onDuplicate).toHaveBeenCalled();
  });

  it.each([["running", null], ["needs_you", "gate"], ["needs_you", "question"], ["needs_you", "cap"], ["done", null], ["paused", null]])("renders nothing for %s (%s)", (display_status, kind) => {
    stubFetch();
    // A question with its text has the question card (Banner's QuestionCard); without it, the card below.
    const { container } = routed(<StateCard item={detail({ display_status: display_status as never, stop: kind ? stop(kind) : null, needs_context_question: "Keep the header?" })} {...handlers()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PausedCard", () => {
  const paused = (hook: string) => ({ id: hook, hook_point: hook, status: "paused", node_id: "verification" }) as WorkerSession;

  it("resumes with the steer, or without one", async () => {
    const calls = stubFetch(WRITES);
    const reload = vi.fn();
    render(<PausedCard item={detail({ display_status: "paused", worker_sessions: [paused("verification.review.code_review")] })} reload={reload} />);
    await userEvent.type(screen.getByLabelText("Steer"), "look at reindex first");
    await userEvent.click(screen.getByRole("button", { name: "Resume with steer" }));
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls).map((c) => c.body)).toEqual([{ steer: "look at reindex first" }, { steer: null }]);
  });

  it("resumes with the steer on ⌘↵, and not with an empty one", async () => {
    const calls = stubFetch(WRITES);
    render(<PausedCard item={detail({ display_status: "paused", worker_sessions: [paused("verification.review.code_review")] })} reload={() => {}} />);
    await userEvent.click(screen.getByLabelText("Steer"));
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(posts(calls)).toEqual([]);
    await userEvent.type(screen.getByLabelText("Steer"), "look at reindex first");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(posts(calls).map((c) => c.body)).toEqual([{ steer: "look at reindex first" }]));
  });

  it("offers plain Resume only when the item is not steerable, since the server refuses a steer then", async () => {
    const calls = stubFetch(WRITES);
    const reload = vi.fn();
    render(<PausedCard item={detail({ display_status: "paused", steerable: false, worker_sessions: [paused("verification.review.code_review"), paused("verification.review.automated_review")] })} reload={reload} />);
    expect(screen.queryByLabelText("Steer")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume with steer" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls).map((c) => c.body)).toEqual([{ steer: null }]);
  });

  it("sends the steer to one paused task when several are paused and one is picked", async () => {
    const calls = stubFetch(WRITES);
    render(<PausedCard item={detail({ display_status: "paused", worker_sessions: [paused("verification.review.code_review"), paused("verification.review.automated_review")] })} reload={() => {}} />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "verification.review.automated_review");
    await userEvent.type(screen.getByLabelText("Steer"), "only you");
    await userEvent.click(screen.getByRole("button", { name: "Resume with steer" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: null, steers: { "verification.review.automated_review": "only you" } } }]));
  });

  it("for a never-started item is the Not started card, with no steer and no Resume", () => {
    render(<PausedCard item={detail({ display_status: "paused", current_node_id: null, worker_sessions: [] })} reload={() => {}} />);
    expect(screen.getByRole("region", { name: "Not started" })).toHaveTextContent("Nothing has run, and nothing spends tokens until you start it. Start runs it from");
    expect(screen.queryByRole("region", { name: "Paused" })).toBeNull();
    expect(screen.queryByLabelText("Steer")).toBeNull();
    expect(screen.queryByRole("button", { name: /Resume/ })).toBeNull();
  });

  it("is only for a paused item", () => {
    const { container } = render(<PausedCard item={detail({ display_status: "running" })} reload={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
