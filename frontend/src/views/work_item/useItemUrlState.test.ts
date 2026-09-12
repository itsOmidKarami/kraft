import { createElement } from "react";
import { act, render } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
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

/** Same as `renderHook`, plus a Back button — the §2.1b tests need to press
 *  an actual Back, not just call a setter, to tell "pushed" apart from
 *  "replaced": both leave the resulting hash looking identical. */
function renderHookWithBack(hash: string) {
  let latest: ReturnType<typeof useItemUrlState> | undefined;
  function Probe() {
    latest = useItemUrlState(null);
    return null;
  }
  function GoBack() {
    const navigate = useNavigate();
    return createElement(
      "button",
      { onClick: () => navigate(-1), "aria-label": "back" },
      "back",
    );
  }
  const { getByRole } = render(
    createElement(
      MemoryRouter,
      { initialEntries: [`/work-items/w1${hash}`] },
      createElement(GoBack),
      createElement(Probe),
    ),
  );
  return {
    get current() {
      return latest!;
    },
    back: () => act(() => getByRole("button", { name: "back" }).click()),
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

  // spec 2026-09-12 §2.1b: entering fullscreen pushes, so Back only leaves
  // fullscreen — it must not also pop out of the item page in one press.
  it("entering maximized pushes a history entry, so one Back press only leaves fullscreen", () => {
    const h = renderHookWithBack("#node=spec&tab=tasks&session=s1");
    act(() => h.current.setMaximized(true));
    expect(h.current.maximized).toBe(true);

    h.back();
    expect(h.current.maximized).toBe(false);
    expect(h.current.nodeId).toBe("spec"); // still on the item page, not off it
  });

  // spec §2.1b: leaving fullscreen (Restore/Escape) must consume the same
  // entry it pushed on the way in, not add a second one on top of it — or
  // the very next Back press pops back INTO fullscreen instead of off the page.
  it("leaving maximized consumes the entry it pushed, so Back after Restore doesn't reopen it", () => {
    const h = renderHookWithBack("#node=spec&tab=tasks&session=s1");
    act(() => h.current.setMaximized(true));
    act(() => h.current.setMaximized(false)); // Restore/Escape path, not Back
    expect(h.current.maximized).toBe(false);

    h.back();
    expect(h.current.maximized).toBe(false); // did not pop back INTO fullscreen
  });
});
