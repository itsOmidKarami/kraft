import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { layout, type ChainNode } from "./layout";
import { MAP } from "./Minimap";
import { overviewFor, StageGraph } from "./StageGraph";

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

  describe("a chain too long to read whole", () => {
    afterEach(() => vi.restoreAllMocks());
    // Nine nodes, 1330px at 100%: past what the item page shows whole. The item waits at final_review.
    const long: ChainNode[] = [
      { id: "spec", kind: "exec", state: "done" }, { id: "spec_approval", kind: "gate", state: "done" },
      { id: "build", kind: "exec", state: "done" }, { id: "test", kind: "exec", state: "done" }, { id: "describe", kind: "exec", state: "done" },
      { id: "final_review", kind: "gate", state: "current", sub: "needs you", needs: true },
      { id: "open_mr", kind: "exec", state: "todo" }, { id: "await_ci", kind: "exec", state: "todo" }, { id: "merge", kind: "exec", state: "todo" },
    ];
    const overview = () => within(screen.getByRole("group", { name: "Chain overview" }));
    const cam = () => {
      const [, tx, s] = /translate\((-?[\d.]+)px.*scale\(([\d.]+)\)/.exec((document.querySelector(".canvas-world") as HTMLElement).style.transform)!;
      return { tx: Number(tx), s: Number(s) };
    };
    const sized = () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
    };

    it("draws every node under an overview of the whole chain, by name, with one start and one end; a chain that fits has none", () => {
      expect(overviewFor(nodes)).toBe(false);
      const { rerender } = render(<StageGraph name="c" nodes={long} overview names />);
      expect(overview().getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
        "spec, done", "approve spec, done", "build, done", "test, done", "describe, done", "approve final review, needs you", "open mr, not started", "await ci, not started", "merge, not started",
      ]);
      expect(overview().getByRole("button", { name: "approve final review, needs you" })).toHaveClass("is-gate", "is-amber");
      expect(overview().getByRole("button", { name: "merge, not started" })).toHaveClass("is-todo");
      // No grouping below: every node, one ▶ and one ■, and the edges fade instead of counting what is off-screen.
      expect(document.querySelectorAll(".canvas .graph-node")).toHaveLength(9);
      expect(document.querySelectorAll(".mark-start, .mark-end")).toHaveLength(2);
      expect(screen.queryByRole("button", { name: /off-screen/ })).toBeNull();
      expect(document.querySelector(".canvas-fade")).not.toBeNull();
      expect(screen.getByRole("group", { name: "c" })).toContainElement(screen.getByRole("group", { name: "Chain overview" }));
      rerender(<StageGraph name="c" nodes={nodes} overview names />);
      expect(screen.queryByRole("group", { name: "Chain overview" })).toBeNull();
      expect(btn(/^approve merge,/)).toHaveAccessibleName("approve merge, gate, not started");
    });

    it("moves the canvas to a node clicked on the overview, and follows a drag along it", () => {
      sized();
      render(<StageGraph name="c" nodes={long} overview fit={{ floor: 1, left: 12 }} />);
      const at = (id: string) => layout(long).items.find((i) => i.node.id === id)!.cx;
      fireEvent.click(overview().getByRole("button", { name: "describe, done" }));
      // Centred across the 600px view, at the zoom the canvas has.
      const { s } = cam();
      expect(cam().tx).toBeCloseTo(300 - at("describe") * s);
      // The last node cannot be centred: the view stops 40px past the chain's end.
      fireEvent.click(overview().getByRole("button", { name: "merge, not started" }));
      expect(cam().tx).toBeCloseTo(600 - layout(long).W * s - 40);
      const strip = screen.getByRole("group", { name: "Chain overview" });
      const k = Math.min(MAP.K, (600 - 2 * MAP.PAD) / layout(long).W);
      fireEvent.pointerDown(strip, { button: 0, clientX: MAP.PAD + at("build") * k });
      expect(cam().tx).toBeCloseTo(300 - at("build") * s);
      fireEvent.pointerMove(window, { clientX: MAP.PAD + at("test") * k });
      expect(cam().tx).toBeCloseTo(300 - at("test") * s);
      fireEvent.pointerUp(window);
      // The frame marks what the canvas shows: the world from 300px left of test to 300px right of it.
      const frame = document.querySelector(".minimap-frame") as HTMLElement;
      expect(parseFloat(frame.style.left)).toBeCloseTo(MAP.PAD + (at("test") - 300 / s) * k - 3);
      expect(parseFloat(frame.style.width)).toBeCloseTo((600 / s) * k + 6);
    });

    it("enters the overview on where the run stands, moves along it with ←/→, goes down to the node with ↓ and back up with ↑", async () => {
      const user = userEvent.setup();
      render(<StageGraph name="c" nodes={long} overview />);
      const glyph = (name: RegExp) => overview().getByRole("button", { name });
      await user.tab();
      expect(glyph(/^final_review,/)).toHaveFocus();
      await user.keyboard("{ArrowRight}{ArrowRight}");
      expect(glyph(/^await_ci,/)).toHaveFocus();
      await user.keyboard("{ArrowDown}");
      expect(btn(/^await_ci, node/)).toHaveFocus();
      await user.keyboard("{ArrowLeft}{ArrowUp}");
      expect(glyph(/^open_mr,/)).toHaveFocus();
    });
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

    it("opens on the node the run waits at with its next stage whole beside it", () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
      const waiting: ChainNode[] = [...ten.slice(0, 3).map((n) => ({ ...n, state: "done" as const })), { id: "g1", kind: "gate", state: "amber" }, ten[3], ten[4], { id: "g2", kind: "gate", state: "todo" }, ...ten.slice(5, 8)];
      render(<StageGraph name="c" nodes={waiting} fit={{ floor: 1, left: 12 }} focus="current" />);
      // Centred on g1, the view would cut n4 and g2; it slides left until g2 ends 12px in from the edge.
      expect(btn(/^g2,/)).not.toHaveClass("is-clipped");
      expect(btn(/^g1,/)).not.toHaveClass("is-clipped");
      expect(screen.getByRole("button", { name: "3 more nodes off-screen" })).toBeInTheDocument();
    });

    it("counts the gate it waits at as the next one, so it reaches to the gate after that and not to the chain's end", () => {
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1000);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
      const waiting: ChainNode[] = [...ten.slice(0, 3).map((n) => ({ ...n, state: "done" as const })), { id: "g1", kind: "gate", state: "amber" }, ten[3], ten[4], { id: "g2", kind: "gate", state: "todo" }, ...ten.slice(5, 8)];
      render(<StageGraph name="c" nodes={waiting} fit={{ floor: 1, left: 12 }} focus="current" />);
      // The chain's end would fit beside g1 in 1000px, but the view stops at g2, the end of what comes next.
      expect(btn(/^g2,/)).not.toHaveClass("is-clipped");
      expect(btn(/^n7,/)).toHaveClass("is-clipped");
    });

    it("ends the canvas where the pane starts, so nothing is drawn under it", () => {
      narrow();
      expect(screen.getByRole("group", { name: "c" })).toHaveStyle({ right: "380px" });
    });
  });
});
