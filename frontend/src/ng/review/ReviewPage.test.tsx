import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import { DEFAULT_PREFS, useDiffPrefs } from "./prefs";
import { ReviewPage } from "./ReviewPage";

const ITEM = { id: "w1", title: "Cache embeddings", repo: "/r/x", status: "needs_human", display_status: "needs_you", worker_sessions: [], chain_definition: { nodes: [] }, attempts: [], last_review_sha: "h1", head_sha: "h2", pending_gate: "final_review", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" };

let search = "";
const Where = () => {
  search = useLocation().search;
  return null;
};

beforeEach(() => {
  vi.spyOn(api, "getWorkItem").mockResolvedValue(ITEM as never);
  vi.spyOn(api, "getTheme").mockResolvedValue({ diff: DEFAULT_PREFS, code_scheme: { light: "auto", dark: "auto" } } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("ReviewPage", () => {
  it("shows a stale comparison's refusal and offers to compare from the base", async () => {
    vi.spyOn(http, "request").mockImplementation(async (p) =>
      String(p).includes("/compare") ? { status: 404, body: { detail: "no review has been submitted for this gate" } } : { status: 200, body: [] },
    );
    render(
      <MemoryRouter initialEntries={["/work-items/w1/review?to=attempt:2"]}>
        <Routes><Route path="/work-items/:id/review" element={<><ReviewPage /><Where /></>} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("no review has been submitted for this gate")).toBeInTheDocument();
    // A re-review: from defaults to the last review.
    expect(vi.mocked(http.request).mock.calls.map(([p]) => String(p)).find((p) => p.includes("/compare"))).toBe("/work-items/w1/compare?from=last_review&to=attempt%3A2");
    fireEvent.click(screen.getByRole("button", { name: "Compare from base" }));
    expect(search).toBe("?from=base");
  });
});

describe("an ended item's review", () => {
  it.each(["done", "cancelled", "archived"])("is read only when the item is %s: no comment on a file, no Finish review (Kraft-9d8b2.51)", async (display_status) => {
    vi.mocked(api.getWorkItem).mockResolvedValue({ ...ITEM, display_status, pending_gate: null } as never);
    const patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x = 1\n+y = 2\n";
    vi.spyOn(http, "request").mockImplementation(async (p) =>
      String(p).includes("/compare")
        ? { status: 200, body: { from: { target: "base", sha: "b" }, to: { target: "latest", sha: "h" }, rebased: false, files: [{ path: "a.py", insertions: 1, deletions: 0, touched_by: [], viewed: false }], groups: [], diff: patch, untracked: [], truncated: false, ignore_whitespace: false, diff_max_bytes: 1000 } }
        : { status: 200, body: [] },
    );
    render(
      <MemoryRouter initialEntries={["/work-items/w1/review"]}>
        <Routes><Route path="/work-items/:id/review" element={<ReviewPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Read only")).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "a.py" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Comment on this file" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Finish review" })).toBeNull();
  });
});

describe("useDiffPrefs", () => {
  it("saves the whole diff object on each change, and takes it back on a refusal", async () => {
    const req = vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: {} });
    const { result } = renderHook(() => useDiffPrefs());
    await act(async () => {});
    await act(() => result.current.set({ layout: "split" }));
    expect(req).toHaveBeenCalledWith("/theme", expect.objectContaining({ method: "PUT", body: JSON.stringify({ diff: { ...DEFAULT_PREFS, layout: "split" } }) }));
    req.mockResolvedValue({ status: 422, body: { detail: "theme.yaml: diff.layout: bad" } });
    await act(() => result.current.set({ wrap_lines: true }));
    expect(result.current.prefs).toEqual({ ...DEFAULT_PREFS, layout: "split" });
    expect(result.current.error).toBe("theme.yaml: diff.layout: bad");
  });
});

describe("commenting on lines", () => {
  // Old: 1 x, 2 y = 2, 3 w, 4 v. New: 1 x, 2 y = 3, 3 z, 4 w, 5 v.
  const PATCH = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,4 +1,5 @@\n x = 1\n-y = 2\n+y = 3\n+z = 4\n w = 5\n v = 6\n";
  const posts = () =>
    vi.mocked(http.request).mock.calls.filter(([p, i]) => i?.method === "POST" && String(p).endsWith("/threads")).map(([, i]) => JSON.parse(i!.body as string));
  const open = async () => {
    vi.spyOn(http, "request").mockImplementation(async (p, init) =>
      String(p).includes("/compare")
        ? { status: 200, body: { from: { target: "base", sha: "b" }, to: { target: "latest", sha: null }, rebased: false, files: [{ path: "a.py", insertions: 2, deletions: 1, touched_by: [], viewed: false }], groups: [], diff: PATCH, untracked: [], truncated: false, ignore_whitespace: false, diff_max_bytes: 1000 } }
        : init?.method === "POST" ? { status: 201, body: {} } : { status: 200, body: [] },
    );
    render(
      <MemoryRouter initialEntries={["/work-items/w1/review"]}>
        <Routes><Route path="/work-items/:id/review" element={<ReviewPage />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("region", { name: "a.py" });
  };
  const row = (text: string) => [...document.querySelectorAll(".rv-code")].find((c) => c.textContent === text)!.closest<HTMLElement>(".rv-row")!;
  const type = (text: string) => fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), { target: { value: text } });

  it("comments on one line from its +, and Ctrl+Enter adds it to the review", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Comment on line 3" }));
    expect(screen.getByRole("group", { name: "Comment: Line 3" })).toBeInTheDocument();
    type("why z?");
    await act(async () => fireEvent.keyDown(screen.getByRole("textbox", { name: "Comment" }), { key: "Enter", ctrlKey: true }));
    expect(posts()).toEqual([{ body: "why z?", file_path: "a.py", label: null, side: "new", start_line: 3, end_line: 3 }]);
    expect(screen.queryByRole("group", { name: /^Comment: / })).toBeNull();
  });

  it("drops the pick once its comment is added: + on a line of it comments on that line alone", async () => {
    await open();
    fireEvent.mouseDown(screen.getByRole("button", { name: "Comment on line 2" }));
    fireEvent.mouseOver(row("w = 5"));
    fireEvent.mouseUp(window);
    type("three lines");
    await act(async () => fireEvent.keyDown(screen.getByRole("textbox", { name: "Comment" }), { key: "Enter", metaKey: true }));
    expect(document.querySelector(".is-picked")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Comment on line 3" }));
    expect(screen.getByRole("group", { name: "Comment: Line 3" })).toBeInTheDocument();
  });

  it("comments on a range dragged from +", async () => {
    await open();
    fireEvent.mouseDown(screen.getByRole("button", { name: "Comment on line 2" }));
    fireEvent.mouseOver(row("w = 5"));
    fireEvent.mouseUp(window);
    expect(screen.getByRole("group", { name: "Comment: Lines 2–4" })).toBeInTheDocument();
    type("one change, three lines");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(posts()).toEqual([{ body: "one change, three lines", file_path: "a.py", label: null, side: "new", start_line: 2, end_line: 4 }]);
  });

  it("comments on the old side: a removed line, or a context line's old number", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Comment on old line 2" }));
    expect(screen.getByRole("group", { name: "Comment: Old line 2" })).toBeInTheDocument();
    type("was 2 on purpose?");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    fireEvent.click(screen.getByRole("button", { name: "Pick old line 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Comment on old line 3" }));
    type("and w before");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add to review" })));
    expect(posts().map((b) => [b.side, b.start_line, b.end_line])).toEqual([["old", 2, 2], ["old", 3, 3]]);
  });

  it("Esc on an empty composer drops the pick and hands the focus back to the lines", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Comment on line 3" }));
    expect(screen.getByRole("textbox", { name: "Comment" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Comment" }), { key: "Escape" });
    expect(screen.queryByRole("group", { name: /^Comment: / })).toBeNull();
    expect(document.querySelector(".is-picked")).toBeNull();
    expect(screen.getByRole("group", { name: /^Lines of a.py/ })).toHaveFocus();
  });

  it("drops a cancelled draft: nothing is sent, and the line's composer opens empty again", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Comment on line 3" }));
    type("half a thought");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("group", { name: /^Comment: / })).toBeNull();
    // The pick goes with it, and the focus goes back to the file's lines.
    expect(document.querySelector(".is-picked")).toBeNull();
    expect(screen.getByRole("group", { name: /^Lines of a.py/ })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Comment on line 3" }));
    expect(screen.getByRole("textbox", { name: "Comment" })).toHaveValue("");
    expect(posts()).toEqual([]);
  });
});
