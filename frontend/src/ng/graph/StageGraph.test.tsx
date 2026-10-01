import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ChainNode } from "./layout";
import { StageGraph } from "./StageGraph";

const nodes: ChainNode[] = [
  { id: "spec", kind: "exec", state: "done" },
  { id: "approve_spec", kind: "gate", state: "done" },
  { id: "implement", kind: "exec", state: "current", attempt: 2 },
  { id: "merge", kind: "gate", state: "todo", attempt: 1 },
];
const btn = (name: RegExp) => screen.getByRole("button", { name });

describe("StageGraph", () => {
  it("is a group named by the chain, with each node named id, kind, state, attempt", () => {
    render(<StageGraph name="default" nodes={nodes} />);
    expect(screen.getByRole("group", { name: "default" })).toBeInTheDocument();
    expect(btn(/^spec,/)).toHaveAccessibleName("spec, node, done");
    expect(btn(/^approve_spec/)).toHaveAccessibleName("approve_spec, gate, done");
    expect(btn(/^implement/)).toHaveAccessibleName("implement, node, running, attempt 2");
    expect(btn(/^merge/)).toHaveAccessibleName("merge, gate, not started");
  });

  it("enters on the selected node and moves with ←/→, stopping at the ends, through seams", async () => {
    const user = userEvent.setup();
    render(<StageGraph name="c" nodes={nodes} selected="approve_spec" seams={[{ at: 3 }]} />);
    await user.tab();
    expect(btn(/^approve_spec/)).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(btn(/^implement/)).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: "Add a node or gate here" })).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(btn(/^merge/)).toHaveFocus();
    await user.keyboard("{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}");
    expect(btn(/^spec,/)).toHaveFocus();
  });

  it("enters on the current node when nothing is selected", async () => {
    render(<StageGraph name="c" nodes={nodes} />);
    await userEvent.setup().tab();
    expect(btn(/^implement/)).toHaveFocus();
  });

  it("gives the Tab stop back to a new selection", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<StageGraph name="c" nodes={nodes} selected="spec" />);
    await user.tab();
    await user.keyboard("{ArrowRight}");
    rerender(<StageGraph name="c" nodes={nodes} selected="merge" />);
    expect(btn(/^merge/)).toHaveAttribute("tabindex", "0");
    expect(btn(/^approve_spec/)).toHaveAttribute("tabindex", "-1");
  });

  it("opens on Enter, focuses on ⌘Enter or double-click, selects on click, escapes", async () => {
    const user = userEvent.setup();
    const cb = { onOpen: vi.fn(), onFocusNode: vi.fn(), onSelect: vi.fn(), onEscape: vi.fn(), onSeam: vi.fn() };
    render(<StageGraph name="c" nodes={nodes} selected="implement" seams={[{ at: 4, always: true }]} {...cb} />);
    await user.tab();
    await user.keyboard("{Enter}");
    expect(cb.onOpen).toHaveBeenCalledWith("implement");
    await user.keyboard("{Meta>}{Enter}{/Meta}");
    expect(cb.onFocusNode).toHaveBeenCalledWith("implement");
    await user.keyboard("{Escape}");
    expect(cb.onEscape).toHaveBeenCalled();
    await user.dblClick(btn(/^spec,/));
    expect(cb.onFocusNode).toHaveBeenLastCalledWith("spec");
    expect(cb.onSelect).toHaveBeenCalledWith("spec");
    await user.click(screen.getByRole("button", { name: "Add a node or gate here" }));
    expect(cb.onSeam).toHaveBeenCalledWith(4);
  });

  it("calls onBackground for a click on empty canvas, not for a drag", () => {
    const onBackground = vi.fn();
    render(<StageGraph name="c" nodes={nodes} onBackground={onBackground} />);
    const canvas = screen.getByRole("group", { name: "c" });
    fireEvent.click(canvas);
    expect(onBackground).toHaveBeenCalledTimes(1);
    fireEvent.click(btn(/^spec,/));
    expect(onBackground).toHaveBeenCalledTimes(1);
    fireEvent.pointerDown(canvas, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 0 });
    fireEvent.pointerUp(window);
    fireEvent.click(canvas);
    expect(onBackground).toHaveBeenCalledTimes(1);
  });
});
