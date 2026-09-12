import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkItemDiff } from "../../../types";
import { Changes } from "./Changes";

const here = dirname(fileURLToPath(import.meta.url));

function renderChangesTab() {
  const diff: WorkItemDiff = {
    work_item_id: "w1",
    base_ref: "abc",
    files: [{ path: "src/App.tsx", insertions: 3, deletions: 1 }],
    diff: "",
    untracked: [],
    truncated: false,
  };
  return render(
    <Changes diff={diff} diffError={null} selected={null} onSelect={() => {}} />,
  );
}

describe("Changes tab · tree row alignment (Kraft-02ob)", () => {
  it("puts the file name in the stretching track, not the caret", () => {
    renderChangesTab();
    const row = document.querySelector(".tree-row") as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.children[1]).toHaveClass("tree-name");
  });

  it("declares the tree row's grid with the caret's auto track first, not the stretching one", () => {
    // jsdom has no CSS cascade to speak of (styles.order.test.ts's own
    // rationale) -- pinned against the source, same as that file does.
    const css = readFileSync(join(here, "../work_item.css"), "utf-8");
    const rule = css.split(".tree-row {")[1]?.split("}")[0] ?? "";
    expect(rule).toMatch(/grid-template-columns:\s*auto minmax\(0, 1fr\) auto/);
  });
});
