import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByRole("group", { name: "Comment: Lines +5 to +6" })).toBeInTheDocument();
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

  it("offers no suggested change on a range across a gap in the diff, and says why", () => {
    // Lines 5–9 picked, but the diff shows only two of them.
    compose({ path: "search/cache.py", range: { side: "new", start: 5, end: 9 } });
    expect(screen.getByRole("button", { name: "± Suggest change" })).toBeDisabled();
    expect(screen.getByText("No suggestion across lines the diff doesn't show")).toBeInTheDocument();
  });

  it("adds to the review on ⌘↵ from the comment or the suggested change, once there is a comment", async () => {
    const { onSubmit } = compose(NEW);
    const box = screen.getByRole("textbox", { name: "Comment" });
    await act(async () => fireEvent.keyDown(box, { key: "Enter", metaKey: true }));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "Cap it lower" } });
    await act(async () => fireEvent.keyDown(box, { key: "Enter", ctrlKey: true }));
    expect(onSubmit).toHaveBeenCalledWith({ body: "Cap it lower", label: null, suggest: null });
    fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
    await act(async () => fireEvent.keyDown(screen.getByRole("textbox", { name: "Suggested change" }), { key: "Enter", metaKey: true }));
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it("offers no suggested change on the old side, nor on a whole file", () => {
    const { unmount } = compose({ path: "a.py", range: { side: "old", start: 4, end: 4 } });
    expect(screen.getByRole("group", { name: "Comment: Line −4" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "± Suggest change" })).toBeNull();
    unmount();
    compose({ path: "a.py", range: null });
    expect(screen.getByRole("group", { name: "Comment: Comment on this file" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "± Suggest change" })).toBeNull();
  });

  it("names a range across sides by each end's mark, and offers no suggested change on it", () => {
    compose({ path: "calc.py", range: { startSide: "old", start: 2, side: "new", end: 2 } });
    expect(screen.getByRole("group", { name: "Comment: Lines −2 to +2" })).toBeInTheDocument();
    expect(document.querySelector(".rv-range-name")).toHaveTextContent(/^Comment on lines −2 to \+2$/);
    expect([...document.querySelectorAll(".rv-line-chip")].map((c) => c.className)).toEqual(["rv-line-chip is-old", "rv-line-chip is-new"]);
    expect(screen.queryByRole("button", { name: "± Suggest change" })).toBeNull();
  });

  it("says how to comment on several lines when it is on one, and not on a range", () => {
    const { unmount } = compose({ path: "a.py", range: { side: "new", start: 5, end: 5 } });
    expect(document.querySelector(".rv-range-name")).toHaveTextContent(/^Comment on line \+5$/);
    expect(screen.getByText("Drag the + or Shift-click to comment on several lines")).toBeInTheDocument();
    unmount();
    compose(NEW);
    expect(document.querySelector(".rv-range-name")).toHaveTextContent(/^Comment on lines \+5 to \+6$/);
    expect(screen.queryByText(/Drag the \+/)).toBeNull();
  });

  // R10b-11: the ARIA radio group is one tab stop, moved with the arrows; the pencil opens on the checked line.
  it("makes the Label chips one tab stop, moved and picked with the arrows", async () => {
    render(<Composer target={{ path: "calc.py", range: { side: "new", start: 2, end: 2 } }} lines={[]} drafts={new Map()} onSubmit={vi.fn()} onCancel={() => {}} />);
    const group = screen.getByRole("radiogroup", { name: "Label" });
    expect(within(group).getAllByRole("radio").map((r) => r.tabIndex)).toEqual([0, -1, -1, -1]);
    within(group).getByRole("radio", { name: "No label" }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(within(group).getByRole("radio", { name: "Must fix" })).toHaveFocus();
    expect(within(group).getByRole("radio", { name: "Must fix" })).toHaveAttribute("aria-checked", "true");
    expect(within(group).getAllByRole("radio").map((r) => r.tabIndex)).toEqual([-1, 0, -1, -1]);
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    expect(within(group).getByRole("radio", { name: "Nit" })).toHaveFocus();
  });

  it("opens the pencil's menu on the checked start line, not the first", async () => {
    const starts = [
      { at: { side: "new" as const, line: 1 }, text: "def add(a, b):" },
      { at: { side: "old" as const, line: 2 }, text: "    return a - b" },
      { at: { side: "new" as const, line: 2 }, text: "    return a + b" },
    ];
    render(<Composer target={{ path: "calc.py", range: { side: "new", start: 2, end: 2 } }} lines={[]} starts={starts} onStart={vi.fn()} drafts={new Map()} onSubmit={vi.fn()} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Change the start line" }));
    await waitFor(() => expect(screen.getByRole("menuitemradio", { name: /^\+2/ })).toHaveFocus());
  });

  it("changes the start line from the header's pencil, the current one checked", () => {
    const onStart = vi.fn();
    const starts = [
      { at: { side: "new" as const, line: 1 }, text: "def add(a, b):" },
      { at: { side: "old" as const, line: 2 }, text: "    return a - b" },
      { at: { side: "new" as const, line: 2 }, text: "    return a + b" },
    ];
    render(<Composer target={{ path: "calc.py", range: { side: "new", start: 2, end: 2 } }} lines={[]} starts={starts} onStart={onStart} drafts={new Map()} onSubmit={vi.fn()} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Change the start line" }));
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => [i.querySelector(".menu-hint")?.textContent, i.getAttribute("aria-checked")])).toEqual([
      ["def add(a, b):", "false"],
      ["return a - b", "false"],
      ["return a + b", "true"],
    ]);
    fireEvent.click(screen.getByRole("menuitemradio", { name: /^−2/ }));
    expect(onStart).toHaveBeenCalledWith({ side: "old", line: 2 });
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
    expect(JSON.parse(req.mock.calls[0][1]!.body as string)).toEqual({ body: "why z", file_path: "a.py", label: null, side: "new", start_line: 2, end_line: 2, quote: "+z", node_id: "implementation", anchor_sha: "s1" });
  });

  it("posts a range across sides with its start side and the lines it quotes", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { result } = hook([], { target: "latest", sha: null });
    act(() => result.current.openPick({ path: "a.py", anchorSide: "old", anchor: 2, side: "new", head: 2 }));
    const { container } = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "y became z" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(JSON.parse(req.mock.calls[0][1]!.body as string)).toEqual({ body: "y became z", file_path: "a.py", label: null, side: "new", start_line: 2, end_line: 2, start_side: "old", quote: "-y\n+z" });
  });

  it("moves the composer, its text and the pick to the start line the pencil picks", () => {
    const onRetarget = vi.fn();
    const { result } = renderHook(() => useComments({ itemId: "w1", compare: cmp({ target: "latest", sha: null }), files: [file([])], patch, threads: [], reload: () => {}, onRetarget }));
    act(() => result.current.openPick({ path: "a.py", side: "new", anchor: 2, head: 2 }));
    const first = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    fireEvent.change(first.container.querySelector("textarea")!, { target: { value: "half a thought" } });
    fireEvent.click(screen.getByRole("button", { name: "Change the start line" }));
    expect(screen.getAllByRole("menuitemradio").map((i) => i.firstChild?.nextSibling?.textContent)).toEqual(["+1", "−2", "+2"]);
    act(() => fireEvent.click(screen.getByRole("menuitemradio", { name: /^−2/ })));
    expect(onRetarget).toHaveBeenCalledWith({ path: "a.py", range: { startSide: "old", start: 2, side: "new", end: 2 } });
    first.unmount();
    render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    expect(screen.getByRole("group", { name: "Comment: Lines −2 to +2" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Comment" })).toHaveValue("half a thought");
  });

  // R10b-09: the pencil used to drop a typed suggestion with no word. It is kept and
  // the composer says which lines it was written for; across sides it is set aside, unsent.
  it("keeps a typed suggested change when the pencil moves the start, saying which lines it was written for", () => {
    const { result } = renderHook(() => useComments({ itemId: "w1", compare: cmp({ target: "latest", sha: null }), files: [file([])], patch, threads: [], reload: () => {} }));
    act(() => result.current.openPick({ path: "a.py", side: "new", anchor: 2, head: 2 }));
    const first = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    fireEvent.change(first.container.querySelector("textarea")!, { target: { value: "use this" } });
    fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Suggested change" }), { target: { value: "z = CHANGED" } });
    fireEvent.click(screen.getByRole("button", { name: "Change the start line" }));
    act(() => fireEvent.click(screen.getByRole("menuitemradio", { name: /^\+1/ })));
    first.unmount();
    render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    expect(screen.getByRole("textbox", { name: "Comment" })).toHaveValue("use this");
    expect(screen.getByRole("textbox", { name: "Suggested change" })).toHaveValue("z = CHANGED");
    expect(screen.getByRole("status")).toHaveTextContent("Your suggested change was written for line +2. Check that it should replace lines +1 to +2, or remove it.");
  });

  it("sets a typed suggestion aside, unsent, when the pencil makes the range cross sides", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { result } = renderHook(() => useComments({ itemId: "w1", compare: cmp({ target: "latest", sha: null }), files: [file([])], patch, threads: [], reload: () => {} }));
    act(() => result.current.openPick({ path: "a.py", side: "new", anchor: 2, head: 2 }));
    const first = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    fireEvent.change(first.container.querySelector("textarea")!, { target: { value: "use this" } });
    fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Suggested change" }), { target: { value: "z = CHANGED" } });
    fireEvent.click(screen.getByRole("button", { name: "Change the start line" }));
    act(() => fireEvent.click(screen.getByRole("menuitemradio", { name: /^−2/ })));
    first.unmount();
    render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    expect(screen.queryByRole("textbox", { name: "Suggested change" })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("is set aside: a suggestion replaces new lines only");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(JSON.parse(req.mock.calls.at(-1)![1]!.body as string)).not.toHaveProperty("suggestion");
  });

  it("quotes a range thread's lines as they were stored, else as the diff shows them; one line is not quoted", () => {
    const t = (id: string, o: Partial<ReviewThread>) => ({ id, file_path: "a.py", side: "new", start_line: 2, end_line: 2, comments: [], draft: false, state: "open", label: null, ...o }) as unknown as ReviewThread;
    const threads = [t("stored", { start_side: "old", quote: "-y was\n+z was" }), t("older", { start_line: 1 }), t("one", {})];
    const { result } = hook([], { target: "latest", sha: null }, threads);
    render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
    const quotes = screen.getAllByRole("group", { name: "Lines commented on" }).map((q) => [...q.querySelectorAll(".rv-suggest-line")].map((l) => l.textContent));
    expect(quotes).toEqual([["−y was", "+z was"], [" x", "+z"]]);
  });

  it("sends a suggested change only once it differs from the lines it was filled from", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 201, body: {} });
    const { result } = hook([], { target: "latest", sha: null });
    const send = async (suggest: string | null) => {
      act(() => result.current.openPick({ path: "a.py", side: "new", anchor: 2, head: 2 }));
      const view = render(<>{result.current.after("a.py", { side: "new", line: 2 })}</>);
      fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), { target: { value: "why z" } });
      fireEvent.click(screen.getByRole("button", { name: "± Suggest change" }));
      expect(screen.getByRole("textbox", { name: "Suggested change" })).toHaveValue("z");
      if (suggest !== null) fireEvent.change(screen.getByRole("textbox", { name: "Suggested change" }), { target: { value: suggest } });
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
      view.unmount();
      return JSON.parse(req.mock.calls.at(-1)![1]!.body as string);
    };
    expect(await send(null)).not.toHaveProperty("suggestion");
    expect((await send("zz")).suggestion).toEqual({ start_line: 2, end_line: 2, replacement: "zz" });
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

  it("lists a thread on no file as on the whole change, not on a file missing from the comparison", () => {
    const whole = { id: "w", file_path: null, side: null, start_line: null, end_line: null, comments: [], draft: false, state: "open", label: null } as unknown as ReviewThread;
    const { result } = hook([], { target: "latest", sha: null }, [whole]);
    render(<>{result.current.whole}{result.current.elsewhere}</>);
    expect(screen.getByRole("region", { name: "On the whole change" }).querySelectorAll(".rv-thread")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Threads on files not in this comparison" })).toBeNull();
  });
});
