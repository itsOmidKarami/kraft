import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChainStrip, S, stripLayout } from "./ChainStrip";
import { stripRow } from "./stages";
import type { ChainNode } from "./layout";

const nodes: ChainNode[] = [
  { id: "spec", kind: "exec", state: "done" },
  { id: "approve_spec", kind: "gate", state: "done" },
  { id: "implement", kind: "exec", state: "current" },
  { id: "merge", kind: "gate", state: "todo" },
];

describe("ChainStrip", () => {
  it("lays out a column for each node and a pill for each folded stage", () => {
    const lay = stripLayout(stripRow(nodes, new Set(), "implement"));
    expect(lay.items.map((i) => i.node.id)).toEqual(["implement", "merge"]);
    expect(lay.folds.map((f) => [f.fold.words, f.fold.passed])).toEqual([["approve spec", true]]);
    expect(lay.items.map((i) => i.w)).toEqual([S.CE, S.CG]);
    // A column takes its id's width, up to a cap past which the id wraps.
    const wide = stripLayout([{ id: "implementation", kind: "exec" }, { id: "a_very_long_node_name_indeed", kind: "exec" }]);
    expect(wide.items.map((i) => i.w)).toEqual([124, S.MAX]);
    expect(lay.edges).toHaveLength(2);
    expect(lay.edges.map((e) => e.todo)).toEqual([false, true]);
  });

  it("labels every node of the viewed stage and folds the others to a pill named by their gate", () => {
    render(<ChainStrip nodes={nodes} viewing="implement" />);
    expect(screen.getByText("implement")).toBeInTheDocument();
    expect(screen.getByText("merge")).toBeInTheDocument();
    expect(screen.queryByText("spec")).toBeNull();
    expect(screen.getByRole("button", { name: "approve spec stage, 2 nodes, passed: expand" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "merge, gate, not started" })).toBeInTheDocument();
  });

  it("opens a folded stage in place and lands on its first node", async () => {
    const user = userEvent.setup();
    render(<ChainStrip nodes={nodes} viewing="implement" />);
    await user.click(screen.getByRole("button", { name: /^approve spec stage/ }));
    // An id wraps after its underscores, never cut to an ellipsis.
    expect([...document.querySelectorAll(".strip-label")].map((l) => l.textContent)).toContain("approve_\u200bspec");
    expect(screen.queryByRole("button", { name: /^approve spec stage/ })).toBeNull();
    expect(screen.getByRole("button", { name: /^spec, node/ })).toHaveFocus();
  });

  it("enters on the viewed node, moves with ←/→ over nodes and pills, opens on Enter, goes back", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn(), onBack = vi.fn();
    render(<ChainStrip nodes={nodes} viewing="implement" onOpen={onOpen} onBack={onBack} />);
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: /^implement/ })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("button", { name: /^approve spec stage/ })).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}{Enter}");
    expect(onOpen).toHaveBeenCalledWith("merge");
    await user.click(screen.getByRole("button", { name: "Back to the chain" }));
    expect(onBack).toHaveBeenCalled();
  });
});
