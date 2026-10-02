import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Compare, CompareFile, ReviewThread } from "../../types";
import * as http from "../http";
import { useComments } from "./Comments";
import { Composer, type Draft, type Target } from "./Composer";
import { parsePatch } from "./patch";

afterEach(() => vi.restoreAllMocks());

const LINES = ["    def __init__(self, max_items=50_000):", "        self.max_items = max_items"];
const compose = (target: Target, onSubmit = vi.fn(async (_d: Draft): Promise<string | null> => null), drafts = new Map<string, Draft>()) => {
  const onCancel = vi.fn();
  const view = render(<Composer target={target} lines={LINES} drafts={drafts} onSubmit={onSubmit} onCancel={onCancel} />);
  return { onSubmit, onCancel, drafts, ...view };
};
const NEW: Target = { path: "search/cache.py", range: { side: "new", start: 5, end: 6 } };

describe("Composer", () => {
  it("names the range, takes a label and a suggested change prefilled with the lines", async () => {
    const { onSubmit } = compose(NEW);
    expect(screen.getByRole("group", { name: "Comment: Lines 5–6" })).toBeInTheDocument();
    const add = screen.getByRole("button", { name: "Add to review" });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), { target: { value: "Cap it lower" } });
    fireEvent.click(screen.getByRole("radio", { name: "Must fix" }));
    fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
    expect(screen.getByRole("textbox", { name: "Suggested change" })).toHaveValue(LINES.join("\n"));
    fireEvent.change(screen.getByRole("textbox", { name: "Suggested change" }), { target: { value: "x" } });
    await act(async () => fireEvent.click(add));
    expect(onSubmit).toHaveBeenCalledWith({ body: "Cap it lower", label: "must_fix", suggest: "x" });
  });

  it("offers no suggested change on the old side, nor on a whole file", () => {
    const { unmount } = compose({ path: "a.py", range: { side: "old", start: 4, end: 4 } });
    expect(screen.getByRole("group", { name: "Comment: Old line 4" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "± Suggest change" })).toBeNull();
    unmount();
    compose({ path: "a.py", range: null });
    expect(screen.getByRole("group", { name: "Comment: Comment on this file" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "± Suggest change" })).toBeNull();
  });

  it("keeps its text across a remount, and asks before Escape throws it away", () => {
    const drafts = new Map<string, Draft>();
    const first = compose(NEW, undefined, drafts);
    fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), { target: { value: "half a thought" } });
    first.unmount();
    const { onCancel } = compose(NEW, undefined, drafts);
    const box = screen.getByRole("textbox", { name: "Comment" });
    expect(box).toHaveValue("half a thought");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onCancel).toHaveBeenCalled();
    expect(drafts.size).toBe(0);
  });

  it("sends on ⌘↵ or Ctrl+↵ from the comment or the suggestion, never with no text", async () => {
    const { onSubmit } = compose(NEW);
    const box = screen.getByRole("textbox", { name: "Comment" });
    await act(async () => fireEvent.keyDown(box, { key: "Enter", ctrlKey: true }));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "Cap it lower" } });
    // A plain Enter is a new line.
    await act(async () => fireEvent.keyDown(box, { key: "Enter" }));
    expect(onSubmit).not.toHaveBeenCalled();
    await act(async () => fireEvent.keyDown(box, { key: "Enter", metaKey: true }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
    await act(async () => fireEvent.keyDown(screen.getByRole("textbox", { name: "Suggested change" }), { key: "Enter", ctrlKey: true }));
    expect(onSubmit).toHaveBeenCalledTimes(2);
    expect(onSubmit).toHaveBeenLastCalledWith({ body: "Cap it lower", label: null, suggest: LINES.join("\n") });
  });

  it("closes on Escape at once when empty, and shows a refusal in place", async () => {
    const { onCancel, unmount } = compose(NEW);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Comment" }), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
    unmount();
    compose(NEW, vi.fn(async () => "the suggestion's lines must sit inside 5-6"));
    fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), { target: { value: "x" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(screen.getByRole("alert")).toHaveTextContent("the suggestion's lines must sit inside 5-6");
  });
});

describe("useComments", () => {
  const PATCH = `diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 x
-y
+z
`;
  const patch = new Map(parsePatch(PATCH).map((f) => [f.path, f]));
  const file = (touched_by: string[]): CompareFile => ({ path: "a.py", insertions: 1, deletions: 1, touched_by, viewed: false });
  const cmp = (to: Compare["to"]) => ({ to }) as Compare;
  const hook = (touched_by: string[], to: Compare["to"], threads: ReviewThread[] = []) =>
    renderHook(() => useComments({ itemId: "w1", compare: cmp(to), files: [file(touched_by)], patch, threads, reload: () => {} }));

  it("posts the range, the one touching node, and the compared commit off latest", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { result } = hook(["implementation"], { target: "attempt:1", sha: "s1" });
    act(() => result.current.openPick({ path: "a.py", side: "new", anchor: 2, head: 2 }));
    const { container } = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "why z" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(JSON.parse(req.mock.calls[0][1]!.body as string)).toEqual({ body: "why z", file_path: "a.py", label: null, side: "new", start_line: 2, end_line: 2, node_id: "implementation", anchor_sha: "s1" });
  });

  it("leaves the node out with two touchers, and the anchor on latest", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { result } = hook(["implementation", "verify"], { target: "latest", sha: null });
    act(() => result.current.openFile("a.py"));
    const { container } = render(<>{result.current.top("a.py")}</>);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "split this file" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(JSON.parse(req.mock.calls[0][1]!.body as string)).toEqual({ body: "split this file", file_path: "a.py", label: null });
  });

  it("places a thread under its line and lists one with no place in the diff", () => {
    const t = (id: string, file_path: string, end_line: number) => ({ id, file_path, side: "new", start_line: end_line, end_line, comments: [], draft: false, state: "open", label: null }) as unknown as ReviewThread;
    const { result } = hook([], { target: "latest", sha: null }, [t("here", "a.py", 2), t("gone", "a.py", 40), t("other", "b.py", 1)]);
    const { container } = render(<>{result.current.after("a.py", { side: "new", line: 2 })}{result.current.elsewhere}</>);
    expect(container.querySelectorAll(".rv-thread")).toHaveLength(3);
    expect(screen.getByRole("region", { name: "Threads on files not in this comparison" }).querySelectorAll(".rv-thread")).toHaveLength(2);
  });
});
