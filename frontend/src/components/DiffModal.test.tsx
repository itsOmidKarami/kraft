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
});
