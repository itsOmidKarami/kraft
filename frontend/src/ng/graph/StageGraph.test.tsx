import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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

  it("treats the node a run stopped on, or waits at, as where it stands: Tab and the current button reach it (ux2-W5)", async () => {
    const stopped: ChainNode[] = [{ id: "spec", kind: "exec", state: "done" }, { id: "open_mr", kind: "exec", state: "failed" }, { id: "close", kind: "exec", state: "todo" }];
    render(<StageGraph name="c" nodes={stopped} />);
    expect(screen.getByRole("button", { name: /current/i })).toBeInTheDocument();
    await userEvent.setup().tab();
    expect(btn(/^open_mr/)).toHaveFocus();
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
    expect(cb.onSeam).toHaveBeenCalledWith(4, expect.any(HTMLButtonElement));
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

  it("folds a passed stage to one pill, one roving stop, that opens in place on Enter or a click and hands focus to its first node", async () => {
    const user = userEvent.setup();
    render(<StageGraph name="c" nodes={nodes} fold />);
    expect(screen.queryByRole("button", { name: /^spec,/ })).toBeNull();
    const pill = screen.getByRole("button", { name: "approve spec stage, 2 nodes, passed: expand" });
    expect(pill).toHaveAttribute("data-tip", "spec → approve_spec");
    await user.tab();
    expect(btn(/^implement/)).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(pill).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(btn(/^spec,/)).toHaveFocus();
    expect(btn(/^approve_spec/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stage, .*expand$/ })).toBeNull();
  });

  describe("with a view narrower than the chain", () => {
    afterEach(() => vi.restoreAllMocks());
    const ten: ChainNode[] = Array.from({ length: 10 }, (_, i) => ({ id: `n${i}`, kind: "exec", state: "todo" }));
    const narrow = () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
      render(<StageGraph name="c" nodes={ten} fit={{ floor: 0.6, left: 12 }} reserve={380} />);
    };

    it("counts the nodes it cuts off at an edge, and a click slides them in", async () => {
      narrow();
      // At 60% from 12px in, n0..n5 end by x = 560; n6..n9 run past 600.
      // The column the edge cuts is not drawn, so no label shows cut mid-word.
      expect(btn(/^n6,/)).toHaveClass("is-clipped");
      expect(btn(/^n5,/)).not.toHaveClass("is-clipped");
      await userEvent.click(screen.getByRole("button", { name: "4 more nodes off-screen" }));
      expect(screen.getByRole("button", { name: "4 earlier nodes off-screen" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /more nodes? off-screen/ })).toBeNull();
    });

    it("counts a folded stage past the edge by its nodes", () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
      // n0..n2 and a gate end the first stage, the run stands on n3; the last stage, three nodes past the next, folds.
      const staged: ChainNode[] = [...ten.slice(0, 3).map((n) => ({ ...n, state: "done" as const })), { id: "g1", kind: "gate", state: "done" }, { ...ten[3], state: "current" }, { id: "g2", kind: "gate" }, ten[4], { id: "g3", kind: "gate" }, ...ten.slice(5, 8)];
      render(<StageGraph name="c" nodes={staged} fit={{ floor: 1, left: 12 }} fold />);
      expect(screen.getByRole("button", { name: "n7 stage, 3 nodes, not started: expand" })).toHaveClass("is-clipped");
      // From 12px in at 100%: the first stage's pill (112), n3, g2 and n4 end by x = 582; g3 and the last pill run past 600.
      expect(screen.getByRole("button", { name: "4 more nodes off-screen" })).toBeInTheDocument();
    });

    it("opens on the node the run waits at with its next stage whole beside it", () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
      const waiting: ChainNode[] = [...ten.slice(0, 3).map((n) => ({ ...n, state: "done" as const })), { id: "g1", kind: "gate", state: "amber" }, ten[3], ten[4], { id: "g2", kind: "gate", state: "todo" }, ...ten.slice(5, 8)];
      render(<StageGraph name="c" nodes={waiting} fit={{ floor: 1, left: 12 }} focus="current" fold />);
      // Centred on g1, the view would cut n4 and g2; it slides left until g2 ends 12px in from the edge.
      expect(btn(/^g2,/)).not.toHaveClass("is-clipped");
      expect(btn(/^g1,/)).not.toHaveClass("is-clipped");
      expect(screen.getByRole("button", { name: "3 more nodes off-screen" })).toBeInTheDocument();
    });

    it("ends the canvas where the pane starts, so nothing is drawn under it", () => {
      narrow();
      expect(screen.getByRole("group", { name: "c" })).toHaveStyle({ right: "380px" });
    });
  });
});
