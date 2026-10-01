import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FixTarget, ReviewComment, ReviewThread, WorkItem } from "../../types";
import * as http from "../http";
import { BottomBar, FinishDialog, useSubmit } from "./FinishReview";
import { approveBlock, outcomes } from "./finish";

const FIX: FixTarget = { gate: "final_review", node: "implementation", then: ["verification", "work_brief", "local_review", "merge_request", "summary"], round: { n: 2, max: 3 } };
const item = (o: Partial<WorkItem> = {}) => ({ id: "w1", pending_gate: "final_review", display_status: "needs_you", fix_target: FIX, ...o }) as WorkItem;
const c = (o: Partial<ReviewComment> = {}): ReviewComment => ({ id: "c1", thread_id: "t1", review_id: null, author: "you", attempt: null, body: "Cap it", suggestion: null, claim: null, created_at: "t", draft: true, ...o });
const th = (o: Partial<ReviewThread> = {}): ReviewThread => ({ id: "t1", work_item_id: "w1", gate: "final_review", node_id: null, file_path: "a.py", side: "new", start_line: 4, end_line: 4, anchor_sha: "h", label: null, state: "open", resolved_at: null, created_at: "t", comments: [c()], draft: true, ...o });
const text = (rows: ReturnType<typeof outcomes>) => rows.map((r) => [r.key, r.parts.map((p) => p.t).join(""), r.disabled]);

afterEach(() => vi.restoreAllMocks());

describe("outcomes", () => {
  it("builds request changes from fix_target, shortening a long path", () => {
    expect(text(outcomes(item(), "final_review", [th()], FIX))).toEqual([
      ["request_changes", "Sends these threads back to implementation, the node this item runs a fix round on. Chain resumes from there: verification → work_brief → … → final_review. Fix round 2 of 3.", false],
      ["comment", "Agents answer questions in-thread. No code runs; the gate stays open.", false],
      ["approve", "Marks final_review approved, same as Approve on the gate.", false],
    ]);
  });

  it("gateless: from /fix-target, with its reason and no round; comment keeps the item going; no approve", () => {
    const fix: FixTarget = { gate: null, node: "implementation", then: ["verification"], round: null, reason: "threads on a.py" };
    expect(text(outcomes(item({ pending_gate: null }), null, [th()], fix))).toEqual([
      ["request_changes", "Sends these threads back to implementation, the node this item runs a fix round on. Chain resumes from there: verification. Why this node: threads on a.py.", false],
      ["comment", "Agents answer questions in-thread. No code runs and the item keeps going.", false],
      ["approve", "Available when the item is waiting at a gate.", true],
    ]);
  });

  it("turns request changes off with no rounds left, and on an ended item", () => {
    expect(text(outcomes(item(), "final_review", [], { ...FIX, round: { n: 4, max: 3 } }))[0]).toEqual(["request_changes", expect.stringMatching(/No fix rounds left\.$/), true]);
    expect(text(outcomes(item({ display_status: "done" }), "final_review", [], FIX))[0]).toEqual(["request_changes", "Unavailable once the item is done, cancelled or archived.", true]);
  });
});

describe("approveBlock", () => {
  it("allows approve only at that pending gate with no must-fix open, a draft one included", () => {
    expect(approveBlock(item(), "final_review", [th({ label: "must_fix", state: "resolved" })])).toBeNull();
    expect(approveBlock(item(), "local_review", [])).toBe("Available when the item is waiting at a gate.");
    expect(approveBlock(item({ pending_gate: null }), "final_review", [])).toBe("Available when the item is waiting at a gate.");
    expect(approveBlock(item(), "final_review", [th({ label: "must_fix" })])).toBe("Unavailable while must-fix threads are open.");
    expect(approveBlock(item(), "final_review", [th({ label: "must_fix", draft: false, state: "claimed" })])).toBe("Unavailable while must-fix threads are open.");
  });
});

let where = "";
const Where = () => {
  where = useLocation().pathname + useLocation().search;
  return null;
};
const routed = (ui: ReactNode) => render(<MemoryRouter initialEntries={["/work-items/w1/review"]}><Routes><Route path="*" element={<>{ui}<Where /></>} /></Routes></MemoryRouter>);

function Finish({ it = item(), gate = "final_review" as string | null, threads = [th()], reload = () => {}, initial, digest }: { it?: WorkItem; gate?: string | null; threads?: ReviewThread[]; reload?: () => void; initial?: "request_changes"; digest?: string }) {
  const submit = useSubmit(it, gate, threads, reload, digest);
  return <FinishDialog item={it} gate={gate} threads={threads} initial={initial} submit={submit} onClose={() => {}} />;
}
const body = () => JSON.parse(vi.mocked(http.request).mock.calls.at(-1)![1]!.body as string);

describe("FinishDialog", () => {
  it("requests changes at the gate, then shows the item with the fix node selected", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: { status: "active" } });
    routed(<Finish />);
    expect(screen.getByRole("radio", { name: /Request changes/ })).toBeChecked();
    fireEvent.change(screen.getByRole("textbox", { name: "Overall note" }), { target: { value: " Bound it. " } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Submit review" })));
    expect(vi.mocked(http.request).mock.calls.at(-1)![0]).toBe("/work-items/w1/gates/final_review/review");
    expect(body()).toEqual({ outcome: "request_changes", summary: "Bound it." });
    expect(where).toBe("/work-items/w1?sel=implementation");
  });

  it("gateless: asks /fix-target, posts the item review, and goes to the node the server chose", async () => {
    vi.spyOn(http, "request").mockImplementation(async (p) =>
      String(p).endsWith("/fix-target")
        ? { status: 200, body: { gate: null, node: "verification", then: [], round: null, reason: "current node" } }
        : { status: 200, body: { review_id: "r", outcome: "request_changes", gate: null, target: "verification", target_reason: "current node", action: "rerun" } },
    );
    routed(<Finish it={item({ pending_gate: null, fix_target: null })} gate={null} />);
    expect(await screen.findByText("verification")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Approve/ })).toBeDisabled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Submit review" })));
    expect(vi.mocked(http.request).mock.calls.at(-1)![0]).toBe("/work-items/w1/review");
    expect(body()).toEqual({ outcome: "request_changes" });
    expect(where).toBe("/work-items/w1?sel=verification");
  });

  it("keeps Approve off with an open must-fix, and shows a refusal in the dialog", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 409, body: { detail: "gate 'final_review' is not pending" } });
    routed(<Finish threads={[th({ label: "must_fix" })]} />);
    expect(screen.getByRole("radio", { name: /Approve/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Comment only/ }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Submit review" })));
    expect(screen.getByRole("alert")).toHaveTextContent("gate 'final_review' is not pending");
    expect(where).toBe("/work-items/w1/review");
  });

  it("will not send request changes or a comment with nothing to send", () => {
    routed(<Finish threads={[]} />);
    expect(screen.getByText("No threads yet. Add a comment on a line or a file first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit review" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Approve/ }));
    expect(screen.getByRole("button", { name: "Submit review" })).toBeEnabled();
  });

  it("carries a chain revision's digest on Approve only", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const { unmount } = routed(<Finish threads={[]} digest="d1" />);
    fireEvent.click(screen.getByRole("radio", { name: /Approve/ }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Submit review" })));
    expect(body()).toEqual({ outcome: "approve", digest: "d1" });
    unmount();
    routed(<Finish digest="d1" />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Submit review" })));
    expect(body()).toEqual({ outcome: "request_changes" });
  });

  it("counts your draft replies as things to send", () => {
    routed(<Finish threads={[th({ draft: false, comments: [c({ draft: false, review_id: "r" }), c({ id: "c2", body: "and the TTL?" })] })]} />);
    expect(screen.getByRole("list", { name: "To send" })).toHaveTextContent("and the TTL?");
  });
});

describe("BottomBar", () => {
  const bar = (threads: ReviewThread[], it = item()) => {
    const onFinish = vi.fn();
    function B() {
      return <BottomBar item={it} gate="final_review" threads={threads} onFinish={onFinish} submit={useSubmit(it, "final_review", threads, () => {})} />;
    }
    routed(<B />);
    return onFinish;
  };

  it("before anything is published: drafts by label, agent replies, Finish review", () => {
    const onFinish = bar([th({ label: "must_fix" }), th({ id: "t2" }), th({ id: "t3", draft: false, comments: [c({ draft: false }), c({ id: "a", author: "implementation", draft: false })] })].filter((t) => t.draft));
    expect(screen.getByText("Your review")).toBeInTheDocument();
    expect(screen.getByText("2 pending, 1 must fix, 1 unlabeled")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Finish review" }));
    expect(onFinish).toHaveBeenCalledWith();
  });

  it("once published: resolved count, why Approve is off, and Request changes opens Finish there", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const onFinish = bar([th({ draft: false, label: "must_fix", state: "claimed" }), th({ id: "t2", draft: false, state: "resolved" })]);
    expect(screen.getByText("1 of 2 resolved")).toBeInTheDocument();
    expect(screen.getByText("Approve unlocks when must-fix threads are resolved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Request changes" }));
    expect(onFinish).toHaveBeenCalledWith("request_changes");
  });

  it("approves from the bar when it can", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    bar([th({ draft: false, state: "resolved" })]);
    expect(screen.getByText("Ready to approve")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Approve" })));
    await waitFor(() => expect(where).toBe("/work-items/w1"));
    expect(body()).toEqual({ outcome: "approve" });
  });
});
