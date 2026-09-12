import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkItemDiff } from "../../../types";
import { Diff } from "./Diff";

/** The pane-scroll → tree-highlight sync (G4-05). jsdom never scrolls, so the
 *  test drives the IntersectionObserver callback directly — the global stub in
 *  `test-setup.ts` is a no-op, so this file installs a recording one. */
let fire: ((entries: Partial<IntersectionObserverEntry>[]) => void) | null = null;
let observed: Element[] = [];

class RecordingObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds: ReadonlyArray<number> = [];
  constructor(cb: IntersectionObserverCallback) {
    fire = (entries) => cb(entries as IntersectionObserverEntry[], this);
  }
  observe(el: Element) {
    observed.push(el);
  }
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

const entryFor = (path: string, section: string, top: number) => ({
  isIntersecting: true,
  boundingClientRect: { top } as DOMRectReadOnly,
  target: document.querySelector(
    `[data-section="${section}"] [data-file-header][data-file-path="${path}"]`,
  )!,
});

/** The same path in both sections — the case a bare-path selection conflated. */
const DIFF = {
  base_ref: "abc123",
  diff: "diff --git a/src/a.ts b/src/a.ts\n+++ b/src/a.ts\n+one",
  files: [{ path: "src/a.ts", insertions: 1, deletions: 0 }],
  untracked: [],
  truncated: false,
  landed: {
    commits: ["c1"],
    diff: "diff --git a/src/a.ts b/src/a.ts\n+++ b/src/a.ts\n+old",
    files: [{ path: "src/a.ts", insertions: 1, deletions: 0 }],
    truncated: false,
  },
} as unknown as WorkItemDiff;

beforeEach(() => {
  fire = null;
  observed = [];
  vi.stubGlobal("IntersectionObserver", RecordingObserver);
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RightPane/Diff scroll sync", () => {
  it("reports the top visible file, qualified by its section", () => {
    const onVisibleFile = vi.fn();
    render(<Diff diff={DIFF} diffError={null} selectedFile={null} onVisibleFile={onVisibleFile} />);
    expect(observed.length).toBe(2);

    fire!([entryFor("src/a.ts", "in-flight", 10)]);
    expect(onVisibleFile).toHaveBeenLastCalledWith("src/a.ts");

    fire!([entryFor("src/a.ts", "landed", 10)]);
    expect(onVisibleFile).toHaveBeenLastCalledWith("landed:src/a.ts");
  });

  it("does not scroll the pane back when the selection came from the observer", () => {
    const view = render(
      <Diff diff={DIFF} diffError={null} selectedFile={null} onVisibleFile={() => {}} />,
    );
    (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();

    fire!([entryFor("src/a.ts", "landed", 10)]);
    // The parent echoes the observer's key back in as the selection.
    view.rerender(
      <Diff diff={DIFF} diffError={null} selectedFile="landed:src/a.ts" onVisibleFile={() => {}} />,
    );
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("scrolls to the selected section's own occurrence of a shared path", () => {
    render(
      <Diff diff={DIFF} diffError={null} selectedFile="landed:src/a.ts" onVisibleFile={() => {}} />,
    );
    const target = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock
      .instances[0] as Element;
    expect(target.closest("[data-section]")?.getAttribute("data-section")).toBe("landed");
  });

  it("a click-driven selection still scrolls", async () => {
    render(
      <Diff diff={DIFF} diffError={null} selectedFile="src/a.ts" onVisibleFile={() => {}} />,
    );
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    await userEvent.click(screen.getAllByText("src/a.ts")[0]);
  });
});
