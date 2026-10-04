import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { frameHeight, frameWidth, type ScopesView } from "../item/scopeView";
import { NodeGraph } from "./NodeGraph";
import type { NodeStep } from "./nodeLayout";

afterEach(() => vi.unstubAllGlobals());

const steps: NodeStep[] = [
  { id: "checks", tasks: [{ id: "lint", taskKind: "subprocess", state: "done" }] },
  { id: "tests", tasks: [{ id: "test_changed_scopes", taskKind: "builtin", state: "current" }] },
  { id: "review", tasks: [{ id: "code_review", taskKind: "agent", state: "todo" }] },
];
const chip = (name: string, state: "done" | "failed" = "done") => ({ key: `ws:${name}`, name, command: name, state, meta: "24s" });
const view: ScopesView = {
  path: "verification.tests.test_changed_scopes", round: 1, execution: "sequential",
  rows: [
    { id: "ws", name: "ws", state: "done", note: "done · 48s", chips: [chip("a/**"), chip("b/**")] },
    { id: "pkg", name: "pkg", state: "unreached", note: "not reached", chips: [] },
  ],
};
const expand = { step: "tests", task: "test_changed_scopes", view };
const frame = () => screen.queryByRole("group", { name: /repositories and scopes/ });
const world = () => document.querySelector<HTMLElement>(".canvas-world")!;
const px = (el: HTMLElement, p: "width" | "left" | "height") => parseFloat(el.style[p]);
const reduce = (on: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({ matches: on && query.includes("reduced-motion"), media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));

describe("NodeGraph with a task open", () => {
  it("grows the frame from the task's box while the canvas makes room, then shows its rows", async () => {
    const { rerender } = render(<NodeGraph name="v" steps={steps} />);
    const before = { W: px(world(), "width"), review: px(screen.getByRole("button", { name: /^code_review/ }), "left") };
    rerender(<NodeGraph name="v" steps={steps} expand={expand} />);
    // The frame is the box first: 44 by 44, no rows showing, the world still as it was.
    expect(px(frame()!, "width")).toBe(44);
    expect(px(frame()!, "height")).toBe(44);
    expect(frame()).not.toHaveClass("is-on");
    await waitFor(() => expect(px(frame()!, "width")).toBe(760));
    expect(px(frame()!, "height")).toBe(frameHeight(view));
    expect(frame()).toHaveClass("is-on");
    // Later steps, and the world they sit in, moved right by the frame and its 12px less the column.
    expect(px(world(), "width")).toBe(before.W + 668);
    expect(px(screen.getByRole("button", { name: /^code_review/ }), "left")).toBe(before.review + 668);
    expect(world()).toHaveClass("is-glide");
    await waitFor(() => expect(world()).not.toHaveClass("is-glide"));
  });

  // A row of chips wider than the frame would scroll under a wheel that pans the canvas: the frame takes the row's width.
  it("widens the frame to its longest row of chips, and moves the later steps by as much", async () => {
    const wide = { ...view, rows: [{ ...view.rows[0], chips: ["a", "b", "c", "d", "e"].map((x) => chip(`just test-${x}-with-a-long-name`)) }, view.rows[1]] };
    const before = px(render(<NodeGraph name="v" steps={steps} />).container.querySelector<HTMLElement>(".canvas-world")!, "width");
    document.body.innerHTML = "";
    render(<NodeGraph name="v" steps={steps} expand={{ ...expand, view: wide }} />);
    expect(frameWidth(wide)).toBeGreaterThan(760);
    expect(px(frame()!, "width")).toBe(frameWidth(wide));
    expect(px(world(), "width")).toBe(before + frameWidth(wide) - 92);
  });

  it("does not narrow while it stays open on the round, as its chips finish and read shorter", () => {
    const row = (meta: string) => ({ ...view, rows: [{ ...view.rows[0], chips: ["a", "b", "c", "d", "e"].map((x) => ({ ...chip(`just test-${x}-with-a-long-name`), meta })) }, view.rows[1]] });
    const { rerender } = render(<NodeGraph name="v" steps={steps} expand={{ ...expand, view: row("failed · 1h 12m 30s") }} />);
    const was = px(frame()!, "width");
    expect(frameWidth(row("2s"))).toBeLessThan(was);
    rerender(<NodeGraph name="v" steps={steps} expand={{ ...expand, view: row("2s") }} />);
    expect(px(frame()!, "width")).toBe(was);
  });

  it("fits the camera to the new world once the move is over, not while the world is still moving", async () => {
    const { rerender } = render(<NodeGraph name="v" steps={steps} />);
    const held = world().style.transform;
    rerender(<NodeGraph name="v" steps={steps} expand={expand} />);
    await waitFor(() => expect(px(frame()!, "width")).toBe(760));
    expect(world()).toHaveClass("is-glide");
    expect(world().style.transform).toBe(held);
    await waitFor(() => expect(world()).not.toHaveClass("is-glide"));
    await waitFor(() => expect(world().style.transform).not.toBe(held));
  });

  it("fades the rows first on closing, then moves everything back and takes the frame away", async () => {
    const { rerender } = render(<NodeGraph name="v" steps={steps} expand={expand} />);
    // Open on load: no animation.
    expect(px(frame()!, "width")).toBe(760);
    expect(world()).not.toHaveClass("is-glide");
    rerender(<NodeGraph name="v" steps={steps} />);
    expect(frame()).not.toHaveClass("is-on");
    expect(px(frame()!, "width")).toBe(760);
    await waitFor(() => expect(px(frame()!, "width")).toBe(44));
    expect(frame()).toHaveClass("is-out");
    await waitFor(() => expect(frame()).toBeNull(), { timeout: 2000 });
  });

  it("does not move for someone who asked for less motion: the frame is open or gone at once", async () => {
    reduce(true);
    const { rerender } = render(<NodeGraph name="v" steps={steps} />);
    rerender(<NodeGraph name="v" steps={steps} expand={expand} />);
    expect(px(frame()!, "width")).toBe(760);
    expect(frame()).toHaveClass("is-on");
    expect(world()).not.toHaveClass("is-glide");
    expect(document.querySelector(".canvas")).toHaveClass("is-calm");
    rerender(<NodeGraph name="v" steps={steps} />);
    expect(frame()).toBeNull();
  });

  it("picks a scope, selects the task from its title, and closes", async () => {
    reduce(true);
    const user = userEvent.setup();
    const cb = { onScope: vi.fn(), onSelect: vi.fn(), onCollapse: vi.fn() };
    render(<NodeGraph name="v" steps={steps} expand={expand} {...cb} />);
    const f = frame()!;
    await user.click(within(f).getByRole("button", { name: "b/**, done" }));
    expect(cb.onScope).toHaveBeenCalledWith("ws:b/**");
    // Enter on the focused chip opens its scope too, not the task (R17b-02).
    cb.onScope.mockClear();
    await user.keyboard("{Enter}");
    expect(cb.onScope).toHaveBeenCalledWith("ws:b/**");
    await user.click(within(f).getByRole("button", { name: "test_changed_scopes" }));
    expect(cb.onSelect).toHaveBeenCalledWith({ step: "tests", task: "test_changed_scopes" });
    await user.click(within(f).getByRole("button", { name: /close/ }));
    expect(cb.onCollapse).toHaveBeenCalled();
  });

  it("moves ←/→ along a repository's chips, ↑ to the task title, and ↓ from it into the first chip", async () => {
    reduce(true);
    const user = userEvent.setup();
    render(<NodeGraph name="v" steps={steps} expand={expand} selected={{ step: "tests", task: "test_changed_scopes", scope: "ws:a/**" }} />);
    await user.tab();
    expect(screen.getByRole("button", { name: "a/**, done" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: "b/**, done" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: "b/**, done" })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(within(frame()!).getByRole("button", { name: "test_changed_scopes" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: "a/**, done" })).toHaveFocus();
  });

  it("fades the right edge of a repository's chips when they run past the frame, and not once scrolled to their end", () => {
    reduce(true);
    const w = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(900);
    const c = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(500);
    try {
      render(<NodeGraph name="v" steps={steps} expand={expand} />);
      const row = frame()!.querySelector<HTMLElement>(".scope-chips")!;
      expect(row).toHaveClass("is-more");
      expect(row).toHaveAttribute("tabindex", "-1");
      row.scrollLeft = 400;
      row.dispatchEvent(new Event("scroll"));
      return waitFor(() => expect(row).not.toHaveClass("is-more"));
    } finally {
      w.mockRestore();
      c.mockRestore();
    }
  });

  it("draws a chip's waiting scope as a waiting chip with no time", () => {
    reduce(true);
    const waiting = { ...view, rows: [{ ...view.rows[0], chips: [{ ...chip("a/**"), state: "waiting" as const, meta: "waiting" }] }, view.rows[1]] };
    render(<NodeGraph name="v" steps={steps} expand={{ ...expand, view: waiting }} />);
    expect(within(frame()!).getByRole("button", { name: "a/**, waiting" })).toHaveClass("is-waiting");
  });

  it("marks the scope picked and the task not, and leaves the task's box out of the keys while it is open", () => {
    reduce(true);
    render(<NodeGraph name="v" steps={steps} expand={{ ...expand, scope: "ws:a/**" }} selected={{ step: "tests", task: "test_changed_scopes", scope: "ws:a/**" }} />);
    expect(screen.getByRole("button", { name: "a/**, done" })).toHaveAttribute("aria-pressed", "true");
    expect(within(frame()!).getByRole("button", { name: "test_changed_scopes" })).toHaveAttribute("aria-pressed", "false");
    const box = document.querySelector<HTMLElement>(".graph-node.is-away")!;
    expect(box).toHaveAttribute("aria-hidden", "true");
    expect(box).toHaveAttribute("tabindex", "-1");
  });
});
