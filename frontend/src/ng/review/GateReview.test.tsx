import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { CompareFile, KraftEvent, ReviewThread } from "../../types";
import * as http from "../http";
import { GateReview } from "./GateReview";
import { DEFAULT_PREFS } from "./prefs";
import { ReviewPage } from "./ReviewPage";

const DOC = { work_item_id: "w1", path: ".engineering/reviews/kraft-cb59.md", title: "Review brief", content: "## Summary\n\nAdds an `EmbeddingCache`.", truncated: false, artifact_max_bytes: 524288 };
const FILES: CompareFile[] = [{ path: "search/cache.py", insertions: 9, deletions: 1, touched_by: ["implementation"], viewed: true }];
const must = { id: "t1", label: "must_fix", state: "open", draft: false, file_path: "search/cache.py", comments: [] } as unknown as ReviewThread;

// Waiting at final_review, with a merge request to start and one more gate after it.
const AT = {
  id: "w1", pending_gate: "final_review",
  chain_definition: { template_id: "t", nodes: [{ id: "final_review", kind: "gate" }, { id: "merge_request", kind: "exec" }, { id: "post_merge_review", kind: "gate" }] },
  budget_cap: { spent_usd: 0.04, cap_usd: 10 },
} as never;

afterEach(() => vi.restoreAllMocks());

const overlay = (o: { threads?: ReviewThread[] } = {}) => {
  const p = { approve: vi.fn(async () => null), onReviewChanges: vi.fn(), onRequestChanges: vi.fn(), onClose: vi.fn() };
  render(<GateReview item={AT} gate="final_review" events={[]} doc={{ state: "ready", data: DOC }} files={FILES} threads={o.threads ?? []} isViewed={() => true} {...p} />);
  return p;
};

describe("GateReview", () => {
  it("shows the gate's document beside the changes, viewed marks read-only", async () => {
    overlay();
    expect(await screen.findByText("Review brief")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Summary" })).toBeInTheDocument();
    expect(screen.getByText("WAITING FOR YOU")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Approve final review" })).toBeInTheDocument();
    expect(screen.getByLabelText("Files on the branch")).toBeInTheDocument();
    expect(screen.getByText("Approving starts merge request · 1 gate left · $0.040 of $10.00 spent")).toBeInTheDocument();
    expect(screen.getAllByText("1 file")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /^Viewed / })).toBeNull();
  });

  it("colours a fenced block in the document", async () => {
    render(<GateReview item={AT} gate="final_review" events={[]} doc={{ state: "ready", data: { ...DOC, content: "```ruby\ndef x = 1\n```" } }} files={FILES} threads={[]} isViewed={() => true} approve={vi.fn(async () => null)} onReviewChanges={vi.fn()} onRequestChanges={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText("def", {}, { timeout: 3000 })).toHaveClass("tok-kw");
  });

  it.each([
    ["Ready for you", "passed", "Every finding is addressed."],
    ["Could not decide", "undecided", "The reviewer changed files, so its verdict was ignored: a.py"],
  ])("shows the reviewer's outcome, %s, with its note above the decision", async (word, reason, note) => {
    const events = [
      { seq: 1, work_item_id: "w1", type: "gate_requested", payload: { gate: "final_review" }, node_id: "final_review", created_at: "t" },
      { seq: 2, work_item_id: "w1", type: "gate_auto_review_skipped", payload: { gate: "final_review", reason, note }, node_id: "final_review", created_at: "t" },
    ] as KraftEvent[];
    render(<GateReview item={AT} gate="final_review" events={events} doc={{ state: "ready", data: DOC }} files={FILES} threads={[]} isViewed={() => true} approve={vi.fn(async () => null)} onReviewChanges={vi.fn()} onRequestChanges={vi.fn()} onClose={vi.fn()} />);
    const card = await screen.findByRole("region", { name: "Agent review" });
    expect(card).toHaveTextContent(new RegExp(`^Reviewer:\\s*${word}`));
    expect(card).toHaveTextContent(note);
  });

  it("shows the document's title once and who wrote it (GR-6)", async () => {
    const p = { approve: vi.fn(async () => null), onReviewChanges: vi.fn(), onRequestChanges: vi.fn(), onClose: vi.fn() };
    render(<GateReview item={AT} gate="final_review" events={[]} doc={{ state: "ready", data: { ...DOC, content: "# Review brief\n\n## Summary\n\nAdds it." } }} by="work_item_summary.main.author" files={FILES} threads={[]} isViewed={() => true} {...p} />);
    expect(await screen.findAllByText("Review brief")).toHaveLength(1);
    expect(screen.queryByRole("heading", { level: 1, name: "Review brief" })).toBeNull();
    expect(screen.getByText(/^written by/)).toHaveTextContent("written by work_item_summary.main.author");
  });

  it("offers the document in an editor and its path to copy, as an indexed document does (DV-3)", async () => {
    const calls: [string, string][] = [];
    vi.spyOn(http, "request").mockImplementation((async (url: string, init?: RequestInit) => {
      calls.push([init?.method ?? "GET", url]);
      return url === "/editors" ? { status: 200, body: { available: ["code"], system: false, default: "code" } } : { status: 200, body: {} };
    }) as typeof http.request);
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    const p = { approve: vi.fn(async () => null), onReviewChanges: vi.fn(), onRequestChanges: vi.fn(), onClose: vi.fn() };
    render(<GateReview item={AT} gate="final_review" events={[]} doc={{ state: "ready", data: { ...DOC, absolute_path: "/runs/w1/.engineering/reviews/kraft-cb59.md" } }} files={FILES} threads={[]} isViewed={() => true} {...p} />);
    const open = await screen.findByRole("button", { name: "Open in editor" });
    await vi.waitFor(() => expect(open).toHaveAttribute("title", "Open in VS Code"));
    await act(async () => fireEvent.click(open));
    expect(calls).toContainEqual(["POST", "/work-items/w1/artifact/open"]);
    expect(await screen.findByText("Opened in VS Code.")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy path" })));
    expect(writeText).toHaveBeenCalledWith("/runs/w1/.engineering/reviews/kraft-cb59.md");
  });

  it("says the path to copy by hand when there is no clipboard (R14b-01)", async () => {
    vi.spyOn(http, "request").mockImplementation((async (url: string) => (url === "/editors" ? { status: 200, body: { available: ["code"], system: false, default: "code" } } : { status: 200, body: {} })) as typeof http.request);
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    const toasts: string[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent<{ message: string }>).detail.message);
    window.addEventListener("kraft:toast", on);
    const p = { approve: vi.fn(async () => null), onReviewChanges: vi.fn(), onRequestChanges: vi.fn(), onClose: vi.fn() };
    render(<GateReview item={AT} gate="final_review" events={[]} doc={{ state: "ready", data: { ...DOC, absolute_path: "/runs/w1/.engineering/reviews/kraft-cb59.md" } }} files={FILES} threads={[]} isViewed={() => true} {...p} />);
    await act(async () => fireEvent.click(await screen.findByRole("button", { name: "Copy path" })));
    window.removeEventListener("kraft:toast", on);
    vi.unstubAllGlobals();
    expect(toasts).toEqual(["Couldn't copy. The path is /runs/w1/.engineering/reviews/kraft-cb59.md"]);
  });

  it("approves through the page's review submit", async () => {
    const p = overlay();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Approve" })));
    expect(p.approve).toHaveBeenCalled();
  });

  it("keeps Approve off with an open must-fix; Request changes, a file and × go where they say", async () => {
    const p = overlay({ threads: [must] });
    await screen.findByText("Review brief");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Approve" })).toHaveAccessibleDescription("Unavailable while must-fix threads are open.");
    expect(screen.getByRole("status")).toHaveTextContent("Unavailable while must-fix threads are open.");
    fireEvent.click(screen.getByRole("button", { name: "Request changes" }));
    expect(p.onRequestChanges).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "cache.py" }));
    expect(p.onReviewChanges).toHaveBeenLastCalledWith("search/cache.py");
    fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
    expect(p.onReviewChanges).toHaveBeenLastCalledWith();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(p.onClose).toHaveBeenCalled();
  });
});

describe("the overlay on the review page", () => {
  const ITEM = { id: "w1", title: "t", repo: "/r/x", worker_sessions: [], chain_definition: { nodes: [] }, attempts: [], pending_gate: "final_review", gate_artifact: "x.md", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" };
  const open = async (search: string, o: object = {}, doc: object = DOC) => {
    vi.spyOn(api, "getWorkItem").mockResolvedValue({ ...ITEM, ...o } as never);
    vi.spyOn(api, "getTheme").mockResolvedValue({ diff: DEFAULT_PREFS } as never);
    vi.spyOn(http, "request").mockImplementation(async (p) => (String(p).includes("/artifact") ? { status: 200, body: doc } : String(p).includes("/compare") ? { status: 200, body: { files: [], diff: "", untracked: [], to: { target: "latest", sha: null }, truncated: false } } : { status: 200, body: [] }));
    const view = render(<MemoryRouter initialEntries={[`/work-items/w1/review${search}`]}><Routes><Route path="/work-items/:id/review" element={<ReviewPage />} /></Routes></MemoryRouter>);
    await screen.findByRole("heading", { name: "Review changes: t" });
    return view;
  };

  it("opens only with doc=1, a pending gate and a document", async () => {
    const a = await open("?doc=1");
    expect(screen.getByRole("dialog", { name: "Gate review: final_review" })).toBeInTheDocument();
    a.unmount();
    const b = await open("");
    expect(screen.queryByRole("dialog", { name: /Gate review/ })).toBeNull();
    b.unmount();
    const c = await open("?doc=1", { gate_artifact: null });
    expect(screen.queryByRole("dialog", { name: /Gate review/ })).toBeNull();
    c.unmount();
    await open("?doc=1&gate=local_review");
    expect(screen.queryByRole("dialog", { name: /Gate review/ })).toBeNull();
  });

  it("sends a chain revision's digest with Approve, through the review route (#355)", async () => {
    await open("?doc=1", {}, { ...DOC, digest: "d1" });
    await screen.findByText("Review brief");
    await act(async () => fireEvent.click(within(screen.getByRole("dialog", { name: /Gate review/ })).getByRole("button", { name: "Approve" })));
    const [path, init] = vi.mocked(http.request).mock.calls.at(-1)!;
    expect([path, JSON.parse(init!.body as string)]).toEqual(["/work-items/w1/gates/final_review/review", { outcome: "approve", digest: "d1" }]);
  });
});
