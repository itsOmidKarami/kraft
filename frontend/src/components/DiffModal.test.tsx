import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DiffModal } from "./DiffModal";
import * as api from "../api";

const body = {
  work_item_id: "w1",
  base_ref: "abc1234567",
  files: [{ path: "calc.py", insertions: 2, deletions: 1 }],
  diff: "diff --git a/calc.py b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n context\n",
  untracked: ["extra.py"],
  truncated: false,
};

// Real `git diff` output for a modified file: a `---`/`+++` header pair
// ahead of the hunk, on every changed file — not an edge case.
const headeredDiff =
  "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n";

describe("DiffModal", () => {
  it("classes added, removed and hunk lines", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(body);
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("+new")).toHaveClass("diff-add");
    expect(screen.getByText("-old")).toHaveClass("diff-del");
    expect(screen.getByText("@@ -1 +1,2 @@")).toHaveClass("diff-hunk");
    expect(screen.getByText(" context", { trim: false })).toHaveClass("diff-ctx");
  });

  it("classes file header lines as diff-hunk, not add or del", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({ ...body, diff: headeredDiff });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("--- a/calc.py")).toHaveClass("diff-hunk");
    expect(screen.getByText("+++ b/calc.py")).toHaveClass("diff-hunk");
    expect(screen.getByText("+new")).toHaveClass("diff-add");
    expect(screen.getByText("-old")).toHaveClass("diff-del");
  });

  it("lists untracked files", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(body);
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("extra.py")).toBeInTheDocument();
  });

  it("says when the body was truncated", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({ ...body, truncated: true });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });

  it("rows every changed file and sums the header totals", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body,
      files: [
        { path: "calc.py", insertions: 2, deletions: 1 },
        { path: "hull.py", insertions: 5, deletions: 0 },
      ],
      // both files must actually appear in the diff body: a file named in
      // --numstat but missing from an untruncated diff is not a row (see
      // "marks a file the truncation cut as not shown").
      diff: headeredDiff + "diff --git a/hull.py b/hull.py\n--- a/hull.py\n+++ b/hull.py\n@@ -1 +1,2 @@\n+five\n",
    });
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("calc.py")).toBeInTheDocument();
    expect(screen.getByText("hull.py")).toBeInTheDocument();
    expect(screen.getByText("abc1234567")).toBeInTheDocument();
    // the header sums both rows; a per-file "−1" of its own is not the total
    expect(container.querySelector(".diff-totals")).toHaveTextContent("+7 −1");
  });

  it("shows the error instead of an empty modal when the fetch fails", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockRejectedValue(new Error("no worktree yet"));
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("no worktree yet")).toBeInTheDocument();
    // and not the empty state, which would read as "nothing changed"
    expect(screen.queryByText(/no diff available/i)).toBeNull();
  });

  it("says when there is nothing to diff against", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body, base_ref: null, files: [], diff: "", untracked: [],
    });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/no diff available/i)).toBeInTheDocument();
  });

  it("distinguishes a pinned base with no changes from a missing base", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body, files: [], diff: "", untracked: [],
    });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/no changes yet/i)).toBeInTheDocument();
  });

  // ── per-file collapse (Kraft-bo98) ──────────────────────────────────────

  // Two real chunks, each with its own `---`/`+++` header pair.
  const twoFiles =
    "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n" +
    "diff --git a/hull.py b/hull.py\n--- a/hull.py\n+++ b/hull.py\n@@ -1 +1,2 @@\n+five\n";

  it("renders one collapsible section per file", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body,
      untracked: [],
      files: [
        { path: "calc.py", insertions: 2, deletions: 1 },
        { path: "hull.py", insertions: 5, deletions: 0 },
      ],
      diff: twoFiles,
    });
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("calc.py");
    const details = container.querySelectorAll(".diff-files details");
    expect(details).toHaveLength(2);
    expect(details[0].querySelector("summary")).toHaveTextContent("calc.py");
    expect(details[0].querySelector("summary")).toHaveTextContent("+2");
    expect(details[0].querySelector("summary")).toHaveTextContent("−1");
    expect(details[1].querySelector("summary")).toHaveTextContent("hull.py");
    expect(details[1].querySelector("summary")).toHaveTextContent("+5");
  });

  it("opens every file when the whole diff is small", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body,
      untracked: [],
      files: [
        { path: "calc.py", insertions: 2, deletions: 1 },
        { path: "hull.py", insertions: 5, deletions: 0 },
      ],
      diff: twoFiles,
    });
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("calc.py");
    for (const d of container.querySelectorAll(".diff-files details")) {
      expect(d).toHaveAttribute("open");
    }
  });

  it("collapses a file bigger than the open-line budget", async () => {
    // 400 body lines is over MAX_FILE_LINES (300) on its own, and consumes the
    // whole MAX_OPEN_LINES (600) budget, so the small file after it stays shut
    // too -- one rule, both of the bead's defaults.
    const big =
      "diff --git a/vendor.js b/vendor.js\n--- a/vendor.js\n+++ b/vendor.js\n@@ -1 +1,400 @@\n" +
      Array.from({ length: 400 }, (_, i) => `+line ${i}`).join("\n") +
      "\ndiff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n";
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body,
      untracked: [],
      files: [
        { path: "vendor.js", insertions: 400, deletions: 0 },
        { path: "calc.py", insertions: 2, deletions: 1 },
      ],
      diff: big,
    });
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("vendor.js");
    const details = container.querySelectorAll(".diff-files details");
    expect(details).toHaveLength(2);
    expect(details[0]).not.toHaveAttribute("open");
    expect(details[1]).not.toHaveAttribute("open");
  });

  it("lists untracked files in the same list, marked as content not shown", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(body); // untracked: ["extra.py"]
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    const row = await screen.findByText("extra.py");
    expect(row.closest("li")).toBeInTheDocument();
    expect(row.closest("li")!.querySelector("details")).toBeNull();
    expect(row.closest("li")).toHaveTextContent(/new file — content not shown/);
    // the standalone second list is gone
    expect(container.querySelector(".diff-untracked")).toBeNull();
    expect(screen.queryByText(/New files \(content not shown\)/)).toBeNull();
  });

  it("marks a file the truncation cut as not shown", async () => {
    const cut = {
      ...body,
      untracked: [],
      files: [
        { path: "calc.py", insertions: 2, deletions: 1 },
        { path: "hull.py", insertions: 5, deletions: 0 },
      ],
      diff: "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,2 @@\n-old\n+new\n",
      truncated: true,
    };
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue(cut);
    const first = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    const row = await screen.findByText("hull.py");
    expect(row.closest("li")).toHaveTextContent(/not shown — diff truncated/);
    first.unmount();

    // the same fixture, untruncated: no such row (a rename reports `old => new`
    // in --numstat and `new` in the chunk, and must not print as "truncated")
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({ ...cut, truncated: false });
    render(<DiffModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("calc.py");
    expect(screen.queryByText("hull.py")).toBeNull();
  });

  it("derives counts for a chunk --numstat does not name", async () => {
    // git reports a rename as `old.py => new.py` in --numstat while the chunk
    // is headed `rename to new.py`; the row falls back to counting the chunk.
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      ...body,
      untracked: [],
      files: [{ path: "old.py => new.py", insertions: 1, deletions: 1 }],
      diff:
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n" +
        "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-old\n+new\n",
    });
    const { container } = render(<DiffModal workItemId="w1" onClose={() => {}} />);
    await screen.findByText("new.py");
    const summary = container.querySelector(".diff-files details summary")!;
    expect(summary).toHaveTextContent("+1");
    expect(summary).toHaveTextContent("−1");
  });
});
