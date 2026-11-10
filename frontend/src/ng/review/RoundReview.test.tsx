import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { DEFAULT_PREFS } from "./prefs";
import { ReviewPage } from "./ReviewPage";

const NO_THREADS = { total: 0, open: 0, by_label: { must_fix: 0, question: 0, nit: 0 } };
const ROUND = { n: 1, requested_at: "2026-10-01T00:00:00Z", ended_at: "2026-10-01T01:00:00Z", outcome: "rejected", by: "human", note: null, sent_to: "implementation", reopened: false, reviewer: null, review: { id: "rv", outcome: "request_changes", summary: "Two things before this ships.", head_sha: "h1" }, threads: { ...NO_THREADS, total: 1, open: 1, by_label: { ...NO_THREADS.by_label, must_fix: 1 } }, first_threads: [] };
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

  it("says when the gate has no such round", async () => {
    open("/work-items/w1/review?gate=final_review&round=9");
    expect(await screen.findByText("final_review has no round 9")).toBeInTheDocument();
  });
});
