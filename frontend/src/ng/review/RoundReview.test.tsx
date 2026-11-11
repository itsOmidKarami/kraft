import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { DEFAULT_PREFS } from "./prefs";
import { ReviewPage } from "./ReviewPage";
import { ThreadReadout } from "./RoundReview";
import { buildScenario } from "../../../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../../../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../../../e2e/contract/mocks/ngItems";

const NO_THREADS = { total: 0, open: 0, by_label: { must_fix: 0, question: 0, nit: 0 } };
const ROUND = { n: 1, requested_at: "2026-10-01T00:00:00Z", ended_at: "2026-10-01T01:00:00Z", outcome: "rejected", by: "human", note: null, sent_to: "implementation", verdict: null, reopened: false, reviewer: null, review: { id: "rv", outcome: "request_changes", summary: "Two things before this ships.", head_sha: "h1" }, threads: { ...NO_THREADS, total: 1, open: 1, by_label: { ...NO_THREADS.by_label, must_fix: 1 } }, first_threads: [] };
const ITEM = { id: "w1", title: "Cache embeddings", repo: "/r/x", status: "needs_human", display_status: "needs_you", worker_sessions: [], chain_definition: { nodes: [] }, attempts: [], last_review_sha: null, head_sha: "h2", pending_gate: null, gate_rounds: { final_review: [ROUND] }, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" };
const THREAD = { id: "t1", work_item_id: "w1", gate: "final_review", node_id: null, file_path: "a.py", side: "new", start_side: "new", start_line: 3, end_line: 3, quote: "+ return cache", anchor_sha: "h", label: "must_fix", state: "open", resolved_at: null, created_at: "2026-10-01T00:30:00Z", draft: false, comments: [{ id: "c1", thread_id: "t1", review_id: "rv", author: "you", attempt: null, body: "cache has no size bound", suggestion: null, claim: null, created_at: "2026-10-01T00:30:00Z", draft: false }] };

const open = (at: string) =>
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes><Route path="/work-items/:id/review" element={<ReviewPage />} /></Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.spyOn(api, "getWorkItem").mockResolvedValue(ITEM as never);
  vi.spyOn(api, "getTheme").mockResolvedValue({ diff: DEFAULT_PREFS, code_scheme: { light: "auto", dark: "auto" } } as never);
  vi.spyOn(http, "request").mockImplementation(async (p) => {
    const path = String(p);
    if (path.includes("/threads?gate=final_review&round=1")) return { status: 200, body: [THREAD] };
    if (path.includes("round=9")) return { status: 404, body: { detail: "final_review has no round 9" } };
    return { status: 200, body: [] };
  });
});
afterEach(() => vi.restoreAllMocks());

describe("a round on the review page", () => {
  it("shows its review and threads read-only, after the gate has passed, and reads no comparison", async () => {
    open("/work-items/w1/review?gate=final_review&round=1");
    expect(await screen.findByText("Round 1 of final_review")).toBeInTheDocument();
    expect(screen.getByText("Two things before this ships.")).toBeInTheDocument();
    expect(await screen.findByText("cache has no size bound")).toBeInTheDocument();
    expect(screen.getByLabelText("Lines commented on")).toHaveTextContent("return cache");
    expect(screen.queryByRole("button", { name: /Approve|Request changes|Finish review|Resolve|Reply/ })).toBeNull();
    expect(vi.mocked(http.request).mock.calls.map(([p]) => String(p)).filter((p) => p.includes("/compare"))).toEqual([]);
  });

  // The page's header says how the round ended; the review's own outcome is added only when it is another.
  it.each([
    ["is the round's: said once", "request_changes", 1, false],
    ["is not the round's: said as well", "comment", 1, true],
  ])("a review whose outcome %s", async (_name, outcome, ended, own) => {
    vi.mocked(api.getWorkItem).mockResolvedValue({ ...ITEM, gate_rounds: { final_review: [{ ...ROUND, review: { ...ROUND.review, outcome } }] } } as never);
    const { container } = open("/work-items/w1/review?gate=final_review&round=1");
    await screen.findByText("Round 1 of final_review");
    const summary = container.querySelector(".rv-round-summary")!;
    expect(summary.textContent!.split("changes requested").length - 1).toBe(ended);
    expect(summary.querySelector(".rv-round-outcome")?.textContent ?? null).toBe(own ? "Review: commented" : null);
  });

  it("says when the gate has no such round", async () => {
    open("/work-items/w1/review?gate=final_review&round=9");
    expect(await screen.findByText("final_review has no round 9")).toBeInTheDocument();
  });
});

describe("the demo's round 1", () => {
  it("shows each thread's code on the desktop's round view", async () => {
    const S = buildScenario("default");
    const id = S.ng["needs-gate"];
    const serve = mockServer(S, { ngBoard: true, now: () => NG_NOW });
    const ask = async (path: string) => JSON.parse(((await serve({ method: "GET", url: `http://x/api${path}`, json: () => null })) as Answer).body!);
    await ask(`/work-items/${id}/threads`);
    const threads = await ask(`/work-items/${id}/threads?gate=final_review&round=1`);
    render(<>{threads.map((t: never) => <ThreadReadout key={(t as { id: string }).id} thread={t} />)}</>);
    const quotes = screen.getAllByLabelText("Lines commented on");
    expect(quotes).toHaveLength(2);
    expect(quotes[0]).toHaveTextContent("max(c.ts for c in commits)");
    expect(quotes[1]).toHaveTextContent("self._cache[key] = value");
  });
});
