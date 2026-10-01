import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DisplayStatus, ReviewThread } from "../../../types";
import { detail, stubFetch, type Call } from "../../item/testkit";
import { Toaster } from "../nav/Toaster";
import { GateReviewRoute } from "./GateReview";

const DIFF = [
  "diff --git a/search/cache.py b/search/cache.py", "--- a/search/cache.py", "+++ b/search/cache.py", "@@ -1,2 +1,3 @@",
  " from hashlib import sha256", "+class EmbeddingCache:", "-    old = 1",
  "diff --git a/docs/search.md b/docs/search.md", "--- a/docs/search.md", "+++ b/docs/search.md", "@@ -1,1 +1,2 @@", " # Search", "+## Cache", "",
].join("\n");
const FILES = [
  { path: "search/cache.py", insertions: 58, deletions: 1, touched_by: [], viewed: false },
  { path: "docs/search.md", insertions: 38, deletions: 4, touched_by: [], viewed: false },
];
const thread = (over: Partial<ReviewThread> = {}): ReviewThread => ({
  id: "t1", work_item_id: "w1", gate: "plan_approval", node_id: null, file_path: "search/cache.py", side: "new", start_line: 5, end_line: 5, anchor_sha: "x",
  label: null, state: "open", resolved_at: null, created_at: "2026-09-13T09:00:00Z", draft: false,
  comments: [{ id: "c1", thread_id: "t1", review_id: "r", author: "verification", attempt: 1, body: "No bound on the cache.", suggestion: null, claim: null, created_at: "2026-09-13T09:00:00Z", draft: false }],
  ...over,
});
const gateItem = (over = {}) => detail({ display_status: "needs_you" as DisplayStatus, pending_gate: "plan_approval", current_node_id: "plan_approval", stop: { kind: "gate", node: "plan_approval", resume_at: null, reason: null }, gate_artifact: ".engineering/plans/doc-search-cache.md", head_sha: "abc", fix_target: { gate: "plan_approval", node: "plan", then: ["plan"], round: { n: 1, max: 3 } } as never, ...over });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
function mount(item: ReturnType<typeof detail>, path = "/work-items/w1/review", answers: Record<string, [number, unknown]> = {}) {
  const calls = stubFetch({
    "GET /work-items/w1": [200, item],
    "GET /work-items/w1/compare": [200, { files: FILES, diff: DIFF, untracked: [], truncated: false, diff_max_bytes: 1000000 }],
    "GET /work-items/w1/threads": [200, [thread()]],
    "GET /work-items/w1/artifact": [200, { work_item_id: "w1", path: ".engineering/plans/doc-search-cache.md", title: "Plan · doc-search-cache", content: "# Plan · doc-search-cache\n\n## Tasks\n\n1. Add EmbeddingCache", truncated: false, artifact_max_bytes: 1048576 }],
    ...answers,
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/work-items/:id/review" element={<><GateReviewRoute /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
  return calls;
}
const posts = (calls: Call[]) => calls.filter((c) => c.method !== "GET");
const where = () => screen.getByLabelText("where").textContent;
afterEach(() => vi.unstubAllGlobals());

describe("the gate review (F)", () => {
  it("shows a gate's document and the files that changed, and opens one file at a time", async () => {
    mount(gateItem());
    expect(await screen.findByRole("heading", { level: 1, name: "Plan · doc-search-cache" })).toBeInTheDocument();
    expect(await screen.findByText("Add EmbeddingCache")).toBeInTheDocument();
    expect(screen.getByText(".engineering/plans/doc-search-cache.md")).toBeInTheDocument();
    const cache = screen.getByRole("button", { name: /search\/cache\.py/ });
    const docs = screen.getByRole("button", { name: /docs\/search\.md/ });
    expect(cache).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(cache);
    const diff = screen.getByRole("region", { name: "Diff of search/cache.py" });
    expect(within(diff).getByText("+class EmbeddingCache:")).toHaveClass("ph-diff-add");
    expect(diff.querySelector(".ph-diff-del")?.textContent).toBe("-    old = 1");
    await userEvent.click(docs);
    expect(screen.queryByRole("region", { name: "Diff of search/cache.py" })).toBeNull();
    expect(screen.getByRole("region", { name: "Diff of docs/search.md" })).toBeInTheDocument();
  });

  it("shows an agent's note under its file, labelled, read-only", async () => {
    mount(gateItem());
    await userEvent.click(await screen.findByRole("button", { name: /search\/cache\.py/ }));
    expect(screen.getByText("agent · line 5")).toBeInTheDocument();
    expect(screen.getByText("No bound on the cache.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows no document for a gate without one, and the changes' counts", async () => {
    mount(gateItem({ gate_artifact: null }));
    expect(await screen.findByRole("heading", { level: 1, name: "Design the cache" })).toBeInTheDocument();
    expect(screen.getByText("2 files · +96 −5 · 1 open thread")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Document" })).toBeNull();
    expect(screen.getByRole("button", { name: "Request changes" })).toBeInTheDocument();
  });

  it("says when the diff was cut and a file's patch is missing", async () => {
    mount(gateItem({ gate_artifact: null }), "/work-items/w1/review", { "GET /work-items/w1/compare": [200, { files: FILES, diff: "", untracked: [], truncated: true, diff_max_bytes: 1000 }] });
    await userEvent.click(await screen.findByRole("button", { name: /docs\/search\.md/ }));
    expect(screen.getByText("The diff is not shown (it was cut for size).")).toBeInTheDocument();
  });

  it("shows the agent reviewer's own verdict and nothing when there was none", async () => {
    mount(gateItem(), "/work-items/w1/review", { "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "gate_approved", payload: { gate: "plan_approval", by: "agent", note: "Nothing blocking." }, node_id: "plan_approval", created_at: "2026-09-13T09:00:00Z" }]] });
    expect(await screen.findByText("auto_review · approve")).toBeInTheDocument();
    expect(screen.getByText("Nothing blocking.")).toBeInTheDocument();
  });
});

describe("Approve (F.3)", () => {
  it("sends the review's approval with the document's digest", async () => {
    const calls = mount(gateItem(), "/work-items/w1/review", { "GET /work-items/w1/artifact": [200, { work_item_id: "w1", path: "p.md", title: "Chain revision", content: "x", truncated: false, artifact_max_bytes: 1, digest: "d1g" }], "POST /work-items/w1/gates/plan_approval/review": [200, { outcome: "approve", gate: "plan_approval" }] });
    await screen.findByText("x");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/review", body: { outcome: "approve", digest: "d1g" } }]));
    await waitFor(() => expect(where()).toBe("/work-items/w1"));
  });

  it("is disabled, with the reason, while a must-fix thread is open (mutate approveBlock to see this fail)", async () => {
    mount(gateItem(), "/work-items/w1/review", { "GET /work-items/w1/threads": [200, [thread({ label: "must_fix" })]] });
    const approve = await screen.findByRole("button", { name: "Approve" });
    await waitFor(() => expect(approve).toBeDisabled());
    expect(screen.getByText("Unavailable while must-fix threads are open.")).toBeInTheDocument();
  });

  it("is disabled when the item is not waiting at this gate", async () => {
    mount(gateItem({ pending_gate: "other_gate" }), "/work-items/w1/review?gate=plan_approval");
    expect(await screen.findByText("Available when the item is waiting at a gate.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("keeps a refusal on the screen", async () => {
    mount(gateItem(), "/work-items/w1/review", { "POST /work-items/w1/gates/plan_approval/review": [409, { detail: "gate 'plan_approval' is not pending" }] });
    await screen.findByText("Add EmbeddingCache");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("is not pending");
    expect(where()).toBe("/work-items/w1/review");
  });
});

describe("Request changes (F.3)", () => {
  it("opens a composer with the fix target and sends the review with the note", async () => {
    const calls = mount(gateItem(), "/work-items/w1/review", { "POST /work-items/w1/gates/plan_approval/review": [200, { outcome: "request_changes", gate: "plan_approval" }] });
    await userEvent.click(await screen.findByRole("button", { name: "Reject…" }));
    expect(where()).toBe("/work-items/w1/review?compose=reject");
    expect(screen.getByText("Reject target: plan")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Request changes" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Your note" }), "bound the cache");
    await userEvent.click(send);
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/gates/plan_approval/review", body: { outcome: "request_changes", summary: "bound the cache" } }]));
    // The desktop's submit lands on the item with the fix node selected; the phone shows the item screen.
    await waitFor(() => expect(where()).toBe("/work-items/w1?sel=plan"));
  });

  it("stays on the composer, with the text, when the server refuses", async () => {
    mount(gateItem(), "/work-items/w1/review?compose=reject", { "POST /work-items/w1/gates/plan_approval/review": [409, { detail: "no fix rounds left" }] });
    await userEvent.type(await screen.findByRole("textbox", { name: "Your note" }), "x");
    await userEvent.click(screen.getByRole("button", { name: "Request changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no fix rounds left");
    expect(screen.getByRole("textbox", { name: "Your note" })).toHaveValue("x");
  });
});

describe("the document screen (F.5)", () => {
  it("shows a document over the review, and says when it is gone", async () => {
    mount(gateItem(), "/work-items/w1/review?doc=d1", { "GET /documents/d1": [200, { title: "Spec · cache", path: ".engineering/specs/cache.md", content: "## Goal\n\nCache embeddings." }] });
    expect(await screen.findByRole("heading", { level: 1, name: "Spec · cache" })).toBeInTheDocument();
    expect(screen.getByText(".engineering/specs/cache.md")).toBeInTheDocument();
    expect(screen.getByText("Cache embeddings.")).toBeInTheDocument();
  });
  it("says it is gone on a 404", async () => {
    mount(gateItem(), "/work-items/w1/review?doc=zz", { "GET /documents/zz": [404, { detail: "no such document" }] });
    expect(await screen.findByText("This document is gone.")).toBeInTheDocument();
  });
});
