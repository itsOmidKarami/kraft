import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CompareFile } from "../../types";
import { DiffView, pickRange, type DiffViewProps, type Pick } from "./DiffView";
import { parsePatch } from "./patch";
import { DEFAULT_PREFS, type DiffPrefs } from "./prefs";
import { anchorsOf, buildRows, spans } from "./rows";

const PATCH = `diff --git a/search/cache.py b/search/cache.py
index 1..2 100644
--- a/search/cache.py
+++ b/search/cache.py
@@ -4,4 +4,5 @@ class EmbeddingCache:
 class EmbeddingCache:
-    def __init__(self, max_items=None):
+    def __init__(self, max_items=50_000):
+        self.max_items = max_items
         self._store = OrderedDict()
     def get(self, key):
diff --git a/logo.png b/logo.png
index 1..2 100644
Binary files a/logo.png and b/logo.png differ
`;
const files: CompareFile[] = [
  { path: "search/cache.py", insertions: 2, deletions: 1, touched_by: ["implementation"], viewed: false },
  { path: "logo.png", insertions: 0, deletions: 0, touched_by: ["implementation", "verification"], viewed: false },
];
const patch = new Map(parsePatch(PATCH).map((f) => [f.path, f]));

function View(o: Partial<Omit<DiffViewProps, "prefs">> & { prefs?: Partial<DiffPrefs> }) {
  const [picked, setPicked] = useState<Pick | null>(null);
  return (
    <DiffView
      files={files}
      patch={patch}
      collapsed={new Set()}
      onCollapse={() => {}}
      selected={null}
      isViewed={() => false}
      onViewed={() => {}}
      threadCount={() => 2}
      picked={picked}
      onPick={setPicked}
      onCompose={() => {}}
      onFileComment={() => {}}
      truncated={null}
      {...o}
      prefs={{ ...DEFAULT_PREFS, one_file_at_a_time: false, ...o.prefs }}
    />
  );
}
const row = (text: RegExp) => {
  const code = [...document.querySelectorAll(".rv-code")].filter((c) => text.test(c.textContent ?? ""));
  if (code.length !== 1) throw new Error(`${code.length} lines match ${text}`);
  return code[0].closest(".rv-row, .rv-half") as HTMLElement;
};

afterEach(() => vi.restoreAllMocks());

describe("DiffView", () => {
  it("draws unified lines with both numbers and their marks; binary files say so", () => {
    render(<View />);
    const del = row(/max_items=None/);
    expect(del).toHaveClass("is-del");
    expect([...del.querySelectorAll(".rv-num, .rv-mark")].map((e) => e.textContent)).toEqual(["5", "", "−"]);
    const add = row(/self.max_items = max_items/);
    expect([...add.querySelectorAll(".rv-num, .rv-mark")].map((e) => e.textContent)).toEqual(["", "6", "+"]);
    expect(screen.getByText("@@ -4,4 +4,5 @@ class EmbeddingCache:")).toBeInTheDocument();
    expect(screen.getByText("Binary file, not shown")).toBeInTheDocument();
    expect(screen.getByText("by implementation and verification")).toBeInTheDocument();
  });

  it("marks the changed word on both sides of a pair, in both layouts", () => {
    for (const layout of ["unified", "split"] as const) {
      const { unmount } = render(<View prefs={{ layout }} />);
      expect([...document.querySelectorAll(".rv-word-change")].map((e) => e.textContent)).toEqual(["None", "50_000"]);
      unmount();
    }
    render(<View prefs={{ word_highlight: false }} />);
    expect(document.querySelector(".rv-word-change")).toBeNull();
  });

  it("pairs the split view: the edited line faces its old one, the extra added line faces nothing", () => {
    render(<View prefs={{ layout: "split" }} />);
    const pairs = [...document.querySelectorAll(".rv-row.is-split")].map((r) => [...r.querySelectorAll(".rv-half")].map((h) => h.querySelector(".rv-num")?.textContent ?? ""));
    expect(pairs).toEqual([["4", "4"], ["5", "5"], ["", "6"], ["6", "7"], ["7", "8"]]);
  });

  it("wraps on request, ends a cut diff with its sentence, and says when nothing matches", () => {
    const { unmount } = render(<View prefs={{ wrap_lines: true }} truncated={{ bytes: 524288, files: 3 }} />);
    expect(document.querySelector(".rv-files")).toHaveClass("is-wrap");
    expect(screen.getByText("The diff stops at 512 KB (3 files not shown). The rest is in the worktree.")).toBeInTheDocument();
    unmount();
    render(<View files={[]} />);
    expect(screen.getByText("No files match this comparison.")).toBeInTheDocument();
  });

  it("shows one file at a time when asked", () => {
    render(<View prefs={{ one_file_at_a_time: true }} selected="logo.png" />);
    expect(screen.queryByText("search/cache.py")).toBeNull();
    expect(screen.getByText("logo.png")).toBeInTheDocument();
  });

  it("copies only that file's patch", () => {
    const write = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<View />);
    fireEvent.click(screen.getByRole("button", { name: "File menu for search/cache.py" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy diff" }));
    expect(write).toHaveBeenCalledWith(PATCH.split("diff --git a/logo.png")[0]);
  });

  it("picks by keyboard: arrows move, Shift extends on one side, Enter composes", () => {
    const onCompose = vi.fn();
    render(<View onCompose={onCompose} />);
    const lines = screen.getByRole("group", { name: /^Lines of search\/cache.py/ });
    const key = (k: string, shiftKey = false) => fireEvent.keyDown(lines, { key: k, shiftKey });
    key("ArrowDown"); // the context line: new 4
    key("ArrowDown"); // the removed line: old 5
    key("ArrowDown"); // new 5
    key("ArrowDown", true); // new 6, skipping nothing
    key("Enter");
    expect(onCompose).toHaveBeenLastCalledWith({ path: "search/cache.py", side: "new", anchor: 5, head: 6 });
    expect(row(/max_items=50_000/)).toHaveClass("is-picked");
    expect(row(/max_items=None/)).not.toHaveClass("is-picked");
    // Shift past the other side's line keeps to the pick's side.
    key("ArrowUp");
    key("ArrowUp");
    key("ArrowUp", true);
    key("c");
    expect(onCompose).toHaveBeenLastCalledWith({ path: "search/cache.py", side: "old", anchor: 5, head: 4 });
    // Down from old 4 skips the two added lines to the context line, old 6.
    key("ArrowDown", true);
    key("ArrowDown", true);
    key("Enter");
    expect(onCompose).toHaveBeenLastCalledWith({ path: "search/cache.py", side: "old", anchor: 5, head: 6 });
  });

  it("picks by mouse: a number, then Shift-click extends", () => {
    const onCompose = vi.fn();
    render(<View onCompose={onCompose} />);
    fireEvent.click(within(row(/max_items=50_000/)).getAllByRole("button")[0]);
    fireEvent.click(within(row(/self._store/)).getAllByRole("button")[0], { shiftKey: true });
    fireEvent.keyDown(screen.getByRole("group", { name: /^Lines of search/ }), { key: "Enter" });
    expect(pickRange(onCompose.mock.calls[0][0])).toEqual([5, 7]);
  });

  it("collapses to the header, with the open thread count", () => {
    const onCollapse = vi.fn();
    render(<View collapsed={new Set(["search/cache.py"])} onCollapse={onCollapse} />);
    expect(screen.getByText("2 threads")).toBeInTheDocument();
    expect(document.querySelector(".rv-code")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "collapsed, click to expand" }));
    expect(onCollapse).toHaveBeenCalledWith("search/cache.py", false);
  });
});

describe("rows", () => {
  const pf = parsePatch(PATCH)[0];
  it("puts a context line on both sides, and spans cut a token at a changed range", () => {
    const rows = buildRows(pf, "unified", "python", true);
    expect(anchorsOf(rows[1])).toEqual([{ side: "old", line: 4 }, { side: "new", line: 4 }]);
    expect(anchorsOf(rows[2])).toEqual([{ side: "old", line: 5 }]);
    expect(spans({ text: "abcdef", tokens: [{ text: "abc", cls: "kw" }, { text: "def" }], ranges: [[2, 4]] })).toEqual([
      { text: "ab", cls: "kw", changed: false },
      { text: "c", cls: "kw", changed: true },
      { text: "d", cls: undefined, changed: true },
      { text: "ef", cls: undefined, changed: false },
    ]);
  });
});
