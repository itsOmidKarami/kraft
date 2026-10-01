import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChainStrip, S, stripLayout } from "./ChainStrip";
import type { ChainNode } from "./layout";

const nodes: ChainNode[] = [
  { id: "spec", kind: "exec", state: "done" },
  { id: "approve_spec", kind: "gate", state: "done" },
  { id: "implement", kind: "exec", state: "current" },
  { id: "merge", kind: "gate", state: "todo" },
];

describe("ChainStrip", () => {
  it("keeps the prototype's geometry", () => {
    expect(S).toEqual({ B: 30, CE: 50, CG: 34, CY: 22, PAD: 22, H: 64 });
    const lay = stripLayout(nodes);
    expect(lay.items.map((i) => i.cx)).toEqual([47, 89, 131, 173]);
    expect(lay.W).toBe(22 + 50 + 34 + 50 + 34 + 22);
    expect(lay.edges[0].d).toBe("M65 22 L74 22");
    expect(lay.edges.map((e) => e.todo)).toEqual([false, false, true]);
  });

  it("labels only the viewed node, and names every glyph", () => {
    render(<ChainStrip nodes={nodes} viewing="implement" />);
    expect(screen.getByText("implement")).toBeInTheDocument();
    expect(screen.queryByText("spec")).toBeNull();
    expect(screen.getByRole("button", { name: "approve_spec, gate, done" })).toBeInTheDocument();
  });

  it("enters on the viewed node, moves with ←/→, opens on Enter, goes back", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn(), onBack = vi.fn();
    render(<ChainStrip nodes={nodes} viewing="implement" onOpen={onOpen} onBack={onBack} />);
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: /^implement/ })).toHaveFocus();
    await user.keyboard("{ArrowLeft}{Enter}");
    expect(onOpen).toHaveBeenCalledWith("approve_spec");
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(screen.getByRole("button", { name: /^merge/ })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Back to the chain" }));
    expect(onBack).toHaveBeenCalled();
  });
});
