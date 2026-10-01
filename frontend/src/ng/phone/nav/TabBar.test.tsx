import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useApply } from "../../apply/store";
import { useStore } from "../../../store";
import type { WorkItem } from "../../../types";
import { groupOf } from "../../board/model";
import { TabBar } from "./TabBar";
import { useNeedsCount } from "./needsCount";

const item = (id: string, display_status: WorkItem["display_status"], current_node_id: string | null = "plan"): WorkItem => ({ id, display_status, current_node_id }) as WorkItem;

function Harness() {
  const { pathname } = useLocation();
  return (
    <>
      <output aria-label="where">{pathname}</output>
      <Routes><Route path="*" element={null} /></Routes>
      <TabBar />
    </>
  );
}
const open = (path: string) => {
  window.history.replaceState(null, "", path);
  return render(<BrowserRouter><Harness /></BrowserRouter>);
};

beforeEach(() => useStore.setState({ workItems: { a: item("a", "needs_you"), b: item("b", "failed"), c: item("c", "paused"), d: item("d", "paused", null), e: item("e", "running"), f: item("f", "done") } }));
afterEach(() => window.history.replaceState(null, "", "/"));

describe("TabBar (A.4)", () => {
  it("badges Board with the Needs you count, in its accessible name", () => {
    open("/");
    // needs_you, failed, and paused mid-chain are the board's Needs you group; a paused item with no node is not.
    expect(screen.getByRole("link", { name: "Board, 3 need you" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("counts with the same function the board's group is made of", () => {
    let n = -1;
    function Probe() { n = useNeedsCount(); return null; }
    render(<Probe />);
    const items = Object.values(useStore.getState().workItems);
    expect(n).toBe(items.filter((i) => groupOf(i) === "needs").length);
  });

  it("caps the badge at 99+", () => {
    useStore.setState({ workItems: Object.fromEntries(Array.from({ length: 120 }, (_, i) => [`i${i}`, item(`i${i}`, "needs_you")])) });
    open("/");
    expect(screen.getByText("99+")).toBeInTheDocument();
  });

  it("is hidden on the item, node, review and new-item screens, shown on an area", () => {
    for (const p of ["/work-items/a", "/work-items/a/nodes/plan", "/work-items/a/review", "/work-items/new"]) {
      const { unmount } = open(p);
      expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
      unmount();
    }
    open("/settings/access");
    expect(screen.getByRole("link", { name: "More" })).toHaveAttribute("aria-current", "page");
  });

  it("puts a dot on More while a reload or restart is pending, named in its accessible name", () => {
    useApply.setState({ reload: [{ id: "disk:x", file: "x", text: "x changed" }], restart: [] });
    open("/");
    expect(screen.getByRole("link", { name: "More, 1 pending" })).toBeInTheDocument();
    useApply.setState({ reload: [], restart: [] });
  });

  it("goes to a tab's root with replace, so the stack resets", async () => {
    open("/search");
    const before = (window.history.state as { idx: number }).idx;
    await userEvent.click(screen.getByRole("link", { name: /Analytics/ }));
    await waitFor(() => expect(screen.getByLabelText("where")).toHaveTextContent("/analytics"));
    expect((window.history.state as { idx: number }).idx).toBe(before);
  });

  it("scrolls the content to the top when the active tab is pressed at its root", async () => {
    const scrollTo = (Element.prototype.scrollTo = (() => {}) as never);
    const calls: unknown[] = [];
    Element.prototype.scrollTo = ((o: unknown) => void calls.push(o)) as never;
    const el = document.createElement("div");
    el.className = "ph-content";
    document.body.append(el);
    open("/search");
    await act(async () => void (await userEvent.click(screen.getByRole("link", { name: "Search" }))));
    expect(calls).toEqual([{ top: 0 }]);
    el.remove();
    Element.prototype.scrollTo = scrollTo;
  });
});
