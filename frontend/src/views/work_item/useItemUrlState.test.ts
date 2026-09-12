import { createElement } from "react";
import { act, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useItemUrlState } from "./useItemUrlState";

/** Renders the hook through a real component in a `MemoryRouter`, the way
 *  `index.test.tsx` drives `useNodeSelection`/`useMaximized` — jsdom has no
 *  `window.history` a plain `renderHook` could exercise the hash against. */
function renderHook(hash: string, defaultNodeId: string | null = null) {
  let latest: ReturnType<typeof useItemUrlState> | undefined;
  function Probe() {
    latest = useItemUrlState(defaultNodeId);
    return null;
  }
  render(
    createElement(
      MemoryRouter,
      { initialEntries: [`/work-items/w1${hash}`] },
      createElement(Probe),
    ),
  );
  return {
    get current() {
      return latest!;
    },
  };
}

describe("useItemUrlState", () => {
  it("reads every key out of one hash", () => {
    const s = renderHook("#node=spec&tab=documents&doc=a.md&max=1");
    expect(s.current.nodeId).toBe("spec");
    expect(s.current.tab).toBe("documents");
    expect(s.current.selection).toEqual({ kind: "document", id: "a.md" });
    expect(s.current.maximized).toBe(true);
  });

  it("keeps each tab's selection in its own key, so switching tabs remembers", () => {
    const s = renderHook("#node=verify&tab=changes&file=src/a.ts&doc=b.md");
    expect(s.current.selection).toEqual({ kind: "file", id: "src/a.ts" });
    act(() => s.current.setTab("documents"));
    expect(s.current.selection).toEqual({ kind: "document", id: "b.md" });
  });

  it("defaults the node to the item's current node and the tab to tasks", () => {
    const s = renderHook("", "implementation");
    expect(s.current.nodeId).toBe("implementation");
    expect(s.current.nodeExplicit).toBe(false);
    expect(s.current.tab).toBe("tasks");
  });

  it("clears every tab's selection when the node changes", () => {
    const s = renderHook("#node=spec&tab=tasks&session=s1&file=src/a.ts&doc=b.md");
    act(() => s.current.selectNode("verify"));
    expect(s.current.nodeId).toBe("verify");
    expect(s.current.selection).toEqual({ kind: "session", id: null });
    act(() => s.current.setTab("changes"));
    expect(s.current.selection).toEqual({ kind: "file", id: null });
    act(() => s.current.setTab("documents"));
    expect(s.current.selection).toEqual({ kind: "document", id: null });
  });

  it("keeps the selection when selectNode names the node already shown", () => {
    const s = renderHook("#node=spec&tab=tasks&session=s1");
    act(() => s.current.selectNode("spec"));
    expect(s.current.selection).toEqual({ kind: "session", id: "s1" });
  });

  it("builds a review href that names node, tab and document", () => {
    const s = renderHook("#node=verify&tab=tasks");
    expect(s.current.reviewHref("spec", "documents", "x.md")).toBe("#node=spec&tab=documents&doc=x.md");
  });
});
