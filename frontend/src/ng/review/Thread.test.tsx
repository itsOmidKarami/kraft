import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReviewComment, ReviewThread } from "../../types";
import * as http from "../http";
import { Thread } from "./Thread";

const comment = (o: Partial<ReviewComment> = {}): ReviewComment => ({ id: "c1", thread_id: "t1", review_id: "r1", author: "you", attempt: null, body: "No bound on the cache.", suggestion: null, claim: null, created_at: "t", draft: false, ...o });
const thread = (o: Partial<ReviewThread> = {}): ReviewThread => ({
  id: "t1", work_item_id: "w1", gate: "final_review", node_id: null, file_path: "search/cache.py", side: "new", start_line: 5, end_line: 5,
  anchor_sha: "h", label: "must_fix", state: "open", resolved_at: null, created_at: "t", comments: [comment()], draft: false, ...o,
});
const calls = () => vi.mocked(http.request).mock.calls.map(([p, i]) => [p, i?.method, i?.body]);

afterEach(() => vi.restoreAllMocks());

const show = (t: ReviewThread) => {
  const onChanged = vi.fn();
  const onEdit = vi.fn();
  render(<Thread thread={t} oldLines={() => ["    def __init__(self, max_items=None):"]} onChanged={onChanged} onEdit={onEdit} />);
  return { onChanged, onEdit };
};

describe("Thread", () => {
  it("draws the label, status and body, a suggestion, and an agent's reply with its attempt and claim", () => {
    show(thread({
      comments: [
        comment({ suggestion: { start_line: 5, end_line: 5, replacement: "    def __init__(self, max_items=50_000):" } }),
        comment({ id: "c2", author: "implementation", attempt: 3, claim: "fixed", body: "Bounded at 50k." }),
      ],
      state: "claimed",
    }));
    expect(screen.getByText("MUST FIX")).toBeInTheDocument();
    expect(screen.getByText("claimed")).toBeInTheDocument();
    expect(screen.getByText("Suggested change")).toBeInTheDocument();
    expect([...document.querySelectorAll(".rv-suggest-line")].map((l) => l.textContent)).toEqual(["−    def __init__(self, max_items=None):", "+    def __init__(self, max_items=50_000):"]);
    expect(screen.getByText("implementation")).toBeInTheDocument();
    expect(screen.getByText("attempt 3")).toBeInTheDocument();
    expect(screen.getByText("✓ claimed fixed")).toBeInTheDocument();
  });

  it("lets a draft thread be edited or deleted, nothing else", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 204, body: undefined });
    const { onEdit, onChanged } = show(thread({ draft: true, comments: [comment({ review_id: null, draft: true })] }));
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(onEdit).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls()).toEqual([["/threads/t1", "DELETE", undefined]]);
  });

  it("replies to a published thread as a draft, which can then be deleted", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { onChanged } = show(thread());
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    expect(screen.getByText("Sent with your next review")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Reply" }), { target: { value: " Why 50k? " } });
    fireEvent.click(screen.getByRole("button", { name: "Add reply" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(calls()[0]).toEqual(["/threads/t1/comments", "POST", JSON.stringify({ body: "Why 50k?" })]);
  });

  it("adds a reply, and saves an edited one, on ⌘↵", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    show(thread({ comments: [comment(), comment({ id: "c9", review_id: null, draft: true, body: "and the TTL?" })] }));
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Reply" }), { target: { value: "Why 50k?" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Reply" }), { key: "Enter", metaKey: true });
    await waitFor(() => expect(calls()).toEqual([["/threads/t1/comments", "POST", JSON.stringify({ body: "Why 50k?" })]]));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Edit reply" }), { target: { value: "and the TTL, too?" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Edit reply" }), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(calls()[1]).toEqual(["/comments/c9", "PATCH", JSON.stringify({ body: "and the TTL, too?" })]));
  });

  it("sends a reply, or an edited one, once on a quick second ⌘↵ while the first is in flight", async () => {
    const request = vi.spyOn(http, "request").mockReturnValue(new Promise(() => {}));
    show(thread({ comments: [comment(), comment({ id: "c9", review_id: null, draft: true, body: "and the TTL?" })] }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const edit = screen.getByRole("textbox", { name: "Edit reply" });
    fireEvent.change(edit, { target: { value: "and the TTL, too?" } });
    fireEvent.keyDown(edit, { key: "Enter", metaKey: true });
    fireEvent.keyDown(edit, { key: "Enter", metaKey: true });
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    request.mockClear();
    cleanup();
    show(thread());
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    const box = screen.getByRole("textbox", { name: "Reply" });
    fireEvent.change(box, { target: { value: "Why 50k?" } });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true });
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Add reply" })).toBeDisabled();
  });

  it("deletes your pending reply", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 204, body: undefined });
    show(thread({ comments: [comment(), comment({ id: "c9", review_id: null, draft: true, body: "and the TTL?" })] }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls()).toEqual([["/comments/c9", "DELETE", undefined]]));
  });

  it("resolves an open thread and reopens a resolved one", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const { unmount } = render(<Thread thread={thread()} oldLines={() => []} onChanged={() => {}} onEdit={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    unmount();
    render(<Thread thread={thread({ state: "resolved" })} oldLines={() => []} onChanged={() => {}} onEdit={() => {}} />);
    expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(calls().map(([p]) => p)).toEqual(["/threads/t1/resolve", "/threads/t1/reopen"]));
  });

  it("shows a refusal on the card", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 409, body: { detail: "a draft thread has nothing to resolve yet" } });
    const { onChanged } = show(thread());
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a draft thread has nothing to resolve yet");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
