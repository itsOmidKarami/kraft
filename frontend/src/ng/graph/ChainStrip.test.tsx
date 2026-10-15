import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChainStrip } from "./ChainStrip";
import type { ChainNode } from "./layout";

const nodes: ChainNode[] = [
  { id: "spec", kind: "exec", state: "done" },
  { id: "spec_approval", kind: "gate", state: "done" },
  { id: "implement", kind: "exec", state: "current" },
  { id: "merge", kind: "gate", state: "todo" },
];
const strip = () => within(screen.getByRole("group", { name: "Chain" }));

describe("ChainStrip", () => {
  it("draws the whole chain, one glyph per node in its state, with the node in view marked and nothing folded", () => {
    render(<ChainStrip nodes={nodes} viewing="implement" />);
    expect(strip().getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["spec, node, done", "spec_approval, gate, done", "implement, node, running", "merge, gate, not started"]);
    expect(strip().getByRole("button", { name: /^implement,/ })).toHaveAttribute("aria-current", "step");
    expect(strip().getByRole("button", { name: /^spec,/ })).not.toHaveAttribute("aria-current");
    expect(strip().getByRole("button", { name: /^merge,/ }).querySelector(".map-glyph")).toHaveClass("is-gate", "is-todo");
    // The edge into a node not started is dashed.
    expect([...document.querySelectorAll(".strip-edge")].map((e) => e.classList.contains("is-todo"))).toEqual([false, false, true]);
  });

  it("draws a node with no state, an editor's, plain rather than not started", () => {
    render(<ChainStrip nodes={[{ id: "a", kind: "exec" }, { id: "b", kind: "gate" }]} viewing="a" />);
    expect(strip().getByRole("button", { name: /^b,/ }).querySelector(".map-glyph")).toHaveClass("is-plain");
  });

  it("names nodes in words when asked, as the item page does", () => {
    render(<ChainStrip nodes={nodes} viewing="implement" names />);
    expect(strip().getByRole("button", { name: "approve spec, gate, done" })).toBeInTheDocument();
  });

  it("enters on the viewed node, moves with ←/→, opens on Enter or a click, goes back", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn(), onBack = vi.fn();
    render(<ChainStrip nodes={nodes} viewing="implement" onOpen={onOpen} onBack={onBack} />);
    await user.tab();
    await user.tab();
    expect(strip().getByRole("button", { name: /^implement,/ })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(strip().getByRole("button", { name: /^spec_approval,/ })).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}{Enter}");
    expect(onOpen).toHaveBeenCalledWith("merge");
    await user.click(strip().getByRole("button", { name: /^spec,/ }));
    expect(onOpen).toHaveBeenLastCalledWith("spec");
    await user.click(screen.getByRole("button", { name: "Back to the chain" }));
    expect(onBack).toHaveBeenCalled();
  });
});
