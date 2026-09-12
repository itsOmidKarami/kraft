import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkItemDiff } from "../../../types";
import { Diff } from "./Diff";

/** Two files in-flight, one of them also present (with different content)
 *  under "landed" — the case a bare-path selection used to conflate before
 *  `selection.ts`'s section-qualified file keys. */
const DIFF: WorkItemDiff = {
  work_item_id: "w1",
  base_ref: "abc123",
  diff:
    "diff --git a/src/a.ts b/src/a.ts\n+++ b/src/a.ts\n+one\n" +
    "diff --git a/src/b.ts b/src/b.ts\n+++ b/src/b.ts\n+two",
  files: [
    { path: "src/a.ts", insertions: 1, deletions: 0 },
    { path: "src/b.ts", insertions: 1, deletions: 0 },
  ],
  untracked: [],
  truncated: false,
  landed: {
    commits: ["c1"],
    diff: "diff --git a/src/a.ts b/src/a.ts\n+++ b/src/a.ts\n+old",
    files: [{ path: "src/a.ts", insertions: 1, deletions: 0 }],
    truncated: false,
  },
};

describe("RightPane/Diff (spec 2026-09-12 §1: one file, not the whole diff)", () => {
  it("renders only the selected file, not its siblings", () => {
    render(<Diff diff={DIFF} diffError={null} selectedFile="src/a.ts" />);
    expect(screen.getByText("+one")).toBeInTheDocument();
    expect(screen.queryByText("+two")).toBeNull();
    expect(screen.queryByText("src/b.ts")).toBeNull();
  });

  it("says so when nothing is selected, instead of rendering nothing", () => {
    render(<Diff diff={DIFF} diffError={null} selectedFile={null} />);
    expect(screen.getByText(/select a file/i)).toBeInTheDocument();
    expect(screen.queryByText("+one")).toBeNull();
  });

  it("renders the landed section's own occurrence of a shared path, not the in-flight one", () => {
    render(<Diff diff={DIFF} diffError={null} selectedFile="landed:src/a.ts" />);
    expect(screen.getByText("+old")).toBeInTheDocument();
    expect(screen.queryByText("+one")).toBeNull();
  });

  it("finds a renamed file's chunk even though its tree key is numstat's `{old => new}` notation", () => {
    const renamed: WorkItemDiff = {
      work_item_id: "w1",
      base_ref: "abc123",
      diff: "diff --git a/src/a.ts b/src/b.ts\nsimilarity index 100%\nrename from src/a.ts\nrename to src/b.ts",
      files: [{ path: "src/{a.ts => b.ts}", insertions: 0, deletions: 0 }],
      untracked: [],
      truncated: false,
    };
    render(<Diff diff={renamed} diffError={null} selectedFile="src/{a.ts => b.ts}" />);
    expect(screen.getByText("src/b.ts")).toBeInTheDocument();
    expect(screen.queryByText(/not in the current diff/)).toBeNull();
  });

  it("shows the no-changes state when the diff is genuinely empty, regardless of selection", () => {
    const empty: WorkItemDiff = {
      work_item_id: "w1", base_ref: "abc123", diff: "", files: [], untracked: [], truncated: false,
    };
    render(<Diff diff={empty} diffError={null} selectedFile={null} />);
    expect(screen.getByText(/No changes yet/)).toBeInTheDocument();
  });
});
