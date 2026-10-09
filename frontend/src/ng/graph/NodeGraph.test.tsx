import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { NodeGraph, type NodeSel } from "./NodeGraph";
import { fitCam } from "./camera";
import { nodeLayout } from "./nodeLayout";
import type { Rounds } from "../item/nodeGraph";
import type { NodeStep } from "./nodeLayout";

const steps: NodeStep[] = [
  { id: "checks", tasks: [{ id: "lint", taskKind: "subprocess", state: "done" }, { id: "types", taskKind: "subprocess", state: "done" }, { id: "unit", taskKind: "builtin", state: "done" }] },
  { id: "review", tasks: [{ id: "code_review", taskKind: "agent", state: "current" }] },
  { id: "fix", tasks: [{ id: "a" }, { id: "b" }] },
];
const btn = (name: RegExp | string) => screen.getByRole("button", { name });

describe("NodeGraph", () => {
  it("names tasks by kind and steps as steps, with no parallel count", () => {
    render(<NodeGraph name="verification" steps={steps} />);
    expect(screen.getByRole("group", { name: "verification" })).toBeInTheDocument();
    expect(btn(/^lint/)).toHaveAccessibleName("lint, subprocess task, done");
    expect(btn(/^code_review/)).toHaveAccessibleName("code_review, agent task, running");
    expect(btn("checks, step")).toBeInTheDocument();
    expect(screen.queryByText(/parallel|×3|3 tasks/)).toBeNull();
  });

  it("moves ↑/↓ within a step, ↑ from the top to the step label, ←/→ across at the same row, clamped", async () => {
    const user = userEvent.setup();
    render(<NodeGraph name="v" steps={steps} selected={{ step: "checks", task: "types" }} />);
    await user.tab();
    expect(btn(/^types/)).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(btn(/^unit/)).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(btn(/^unit/)).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(btn(/^code_review/)).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(btn(/^a,/)).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowLeft}");
    expect(btn(/^code_review/)).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(btn(/^lint/)).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(btn("checks, step")).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(btn("review, step")).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(btn(/^code_review/)).toHaveFocus();
  });

  it("opens on Enter, expands on ⌘Enter and double-click, escapes", async () => {
    const user = userEvent.setup();
    const cb = { onOpen: vi.fn(), onExpand: vi.fn(), onEscape: vi.fn(), onSelect: vi.fn() };
    render(<NodeGraph name="v" steps={steps} selected={{ step: "review", task: "code_review" }} {...cb} />);
    await user.tab();
    await user.keyboard("{Enter}");
    expect(cb.onOpen).toHaveBeenCalledWith({ step: "review", task: "code_review" });
    await user.keyboard("{Control>}{Enter}{/Control}{Escape}");
    expect(cb.onExpand).toHaveBeenCalledWith({ step: "review", task: "code_review" });
    expect(cb.onEscape).toHaveBeenCalled();
    await user.dblClick(btn("fix, step"));
    expect(cb.onSelect).toHaveBeenCalledWith({ step: "fix" });
    expect(cb.onExpand).toHaveBeenLastCalledWith({ step: "fix" });
  });

  it("fits the node with 56px kept clear at the foot, for the zoom and round controls", () => {
    const sized = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1040);
    const high = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(440);
    try {
      render(<NodeGraph name="v" steps={steps} />);
      const lay = nodeLayout(steps);
      const cam = fitCam({ W: lay.W, H: lay.H }, { w: 1040, h: 440 - 56 }, "node");
      expect(document.querySelector<HTMLElement>(".canvas-world")!.style.transform).toBe(`translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s})`);
    } finally {
      sized.mockRestore();
      high.mockRestore();
    }
  });

  describe("fix loop", () => {
    const loop = { tone: "active" as const, label: "fix loop · round 2 of 3", tasks: [
      { id: "repair", taskKind: "agent" as const, state: "done" as const, meta: "5m" },
      { id: "judge", taskKind: "agent" as const, state: "todo" as const, meta: "skipped · first repair", faded: true },
    ] };
    const rounds: Rounds = { selected: 2, latest: 2, total: 3, rows: [{ n: 1, tone: "bad", outcome: "sent to the fix loop" }, { n: 2, tone: "warn", outcome: "running" }] };

    it("draws the repair and the judge on the arc as selectable tasks, with the label between them", async () => {
      const user = userEvent.setup();
      const onSelect = vi.fn();
      render(<NodeGraph name="v" steps={steps} loop={loop} onSelect={onSelect} />);
      expect(btn(/^repair/)).toHaveAccessibleName("repair, fix-loop repair agent task, done");
      expect(btn(/^judge/)).toHaveClass("is-faded", "is-todo");
      expect(screen.getByText("fix loop · round 2 of 3")).toBeInTheDocument();
      await user.click(btn(/^judge/));
      expect(onSelect).toHaveBeenCalledWith({ step: "fix_loop", task: "judge" });
      expect(screen.queryByText(/×\d/)).toBeNull();
    });

    it("moves ↓ from a step's last task to the repair, ←/→ between the loop's tasks, ↑ back", async () => {
      const user = userEvent.setup();
      render(<NodeGraph name="v" steps={steps} loop={loop} selected={{ step: "review", task: "code_review" }} />);
      await user.tab();
      await user.keyboard("{ArrowDown}");
      expect(btn(/^repair/)).toHaveFocus();
      await user.keyboard("{ArrowRight}");
      expect(btn(/^judge/)).toHaveFocus();
      await user.keyboard("{ArrowRight}");
      expect(btn(/^judge/)).toHaveFocus();
      await user.keyboard("{ArrowLeft}{ArrowUp}");
      expect(btn(/^b,/)).toHaveFocus();
    });

    it("shows the round beside the zoom, lists the rounds newest first, and picking one reports it", async () => {
      const user = userEvent.setup();
      const onRound = vi.fn();
      render(<NodeGraph name="v" steps={steps} loop={loop} rounds={rounds} onRound={onRound} />);
      const button = btn(/round 2 of 3 · latest/);
      expect(screen.queryByRole("button", { name: /latest ↩/ })).toBeNull();
      await user.click(button);
      expect(screen.getByRole("menu", { name: "Fix loop rounds" })).toBeInTheDocument();
      const rows = screen.getAllByRole("menuitemradio");
      expect(rows.map((r) => [r.textContent, r.getAttribute("aria-checked")])).toEqual([["Round 2 · nowrunning", "true"], ["Round 1sent to the fix loop", "false"]]);
      expect(screen.getByText("limit: 3 rounds · 1 left")).toBeInTheDocument();
      await user.click(rows[1]);
      expect(onRound).toHaveBeenCalledWith(1);
      expect(screen.queryByRole("menu")).toBeNull();
      // The newest round is not a pick: the canvas goes on following it.
      await user.click(button);
      await user.click(screen.getByRole("menuitemradio", { name: /^Round 2/ }));
      expect(onRound).toHaveBeenLastCalledWith(undefined);
    });

    it("tints the button and offers 'latest ↩' while an earlier round is picked", async () => {
      const user = userEvent.setup();
      const onRound = vi.fn();
      render(<NodeGraph name="v" steps={steps} loop={loop} rounds={{ ...rounds, selected: 1 }} onRound={onRound} />);
      expect(btn(/^round 1 of 3$/)).toHaveClass("is-old");
      await user.click(btn(/latest ↩/));
      expect(onRound).toHaveBeenCalledWith(undefined);
    });

    it("lets Escape close the menu before it reaches the canvas", async () => {
      const user = userEvent.setup();
      const onEscape = vi.fn();
      render(<NodeGraph name="v" steps={steps} loop={loop} rounds={rounds} onRound={() => {}} onEscape={onEscape} />);
      await user.click(btn(/round 2 of 3/));
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("menu")).toBeNull();
      expect(onEscape).not.toHaveBeenCalled();
      expect(btn(/round 2 of 3/)).toHaveFocus();
    });

    // `<step>.<task>`: fmt and deps are one loop step's, so they run together.
    const parallel = { label: "round 1", tasks: [{ id: "mend.fmt", label: "fmt", state: "done" as const }, { id: "mend.deps", label: "deps", state: "current" as const, running: true }, { id: "judge" }] };
    /** The canvas with its selection held, as a page holds it: a loop step is open while it, or a task of it, is selected. */
    function Held({ onSelect, ...props }: Omit<ComponentProps<typeof NodeGraph>, "name" | "steps" | "selected" | "onOpen">) {
      const [sel, setSel] = useState<NodeSel>();
      const pick = (s: NodeSel) => { onSelect?.(s); setSel(s); };
      return <NodeGraph name="v" steps={steps} {...props} selected={sel} onSelect={pick} onOpen={pick} />;
    }
    const frame = (step = "mend") => screen.queryByRole("group", { name: `${step}, tasks in parallel` });
    // The step is one control in two places: its box, collapsed, and its frame's head, expanded.
    const box = (step = "mend") => screen.getByRole("button", { name: new RegExp(`^${step},`), expanded: false });
    const head = (step = "mend") => screen.getByRole("button", { name: new RegExp(`^${step},`), expanded: true });
    const noBox = () => expect(screen.queryByRole("button", { name: /^mend,/, expanded: false })).toBeNull();

    it("draws a loop step of several tasks as one box; selecting it opens it into a frame of them, and it folds back", async () => {
      const user = userEvent.setup();
      const onSelect = vi.fn(), onEscape = vi.fn();
      const { container } = render(<Held loop={parallel} onSelect={onSelect} onEscape={onEscape} />);
      // Closed: one stop for the step, in the worst state of its tasks, which are not drawn. The loop is a lane, not an arc.
      expect(box()).toHaveAccessibleName("mend, fix-loop step of 2 parallel tasks, running");
      expect(screen.queryByRole("button", { name: /^fmt/ })).toBeNull();
      expect(container.querySelector(".arc path.is-dashed")!.getAttribute("d")).not.toContain("C");
      expect(container.querySelector(".arc-fork")).toBeNull();
      // A click selects the step, by its own id where its tasks are `mend.<task>`.
      await user.click(box());
      expect(onSelect).toHaveBeenLastCalledWith({ step: "fix_loop", task: "mend" });
      // Open: a frame whose head is the step, named as its box was, selected and focused; its tasks a row apart in
      // one column; and the lane forked: a branch in and one out for each task, its own line cut between the joins.
      await waitFor(() => expect(head()).toHaveFocus());
      expect(head()).toHaveAccessibleName("mend, fix-loop step of 2 parallel tasks, running");
      expect(head()).toHaveAttribute("aria-pressed", "true");
      expect(frame()).toHaveClass("is-on");
      noBox();
      expect(btn(/^fmt/).style.left).toBe(btn(/^deps/).style.left);
      expect(parseFloat(btn(/^deps/).style.top) - parseFloat(btn(/^fmt/).style.top)).toBeCloseTo(92);
      expect(container.querySelectorAll(".arc-fork path")).toHaveLength(4);
      expect(container.querySelector(".arc path.is-dashed")!.getAttribute("clip-path")).toMatch(/^url\(#/);
      // ↓ from the head through its tasks, ←/→ to the next stop on the loop and back to the head.
      await user.keyboard("{ArrowDown}");
      expect(btn(/^fmt/)).toHaveFocus();
      await user.keyboard("{ArrowDown}");
      expect(btn(/^deps/)).toHaveFocus();
      await user.keyboard("{ArrowRight}");
      expect(btn(/^judge/)).toHaveFocus();
      // Esc from outside the step is the page's own, and leaves it open.
      await user.keyboard("{Escape}");
      expect(onEscape).toHaveBeenCalledTimes(1);
      expect(frame()).not.toBeNull();
      await user.keyboard("{ArrowLeft}");
      expect(head()).toHaveFocus();
      // Esc from inside folds it where it stands: the box takes the focus back, still the selection.
      await user.keyboard("{Escape}");
      await waitFor(() => expect(box()).toHaveFocus());
      await waitFor(() => expect(frame()).toBeNull());
      expect(onEscape).toHaveBeenCalledTimes(1);
      expect(box()).toHaveAttribute("aria-pressed", "true");
      // Enter unfolds it from the keys, and its own close folds it.
      await user.keyboard("{Enter}");
      await waitFor(() => expect(head()).toHaveFocus());
      await user.click(screen.getByRole("button", { name: "Close" }));
      await waitFor(() => expect(frame()).toBeNull());
      // A task of it selected keeps it open; its head picked again moves nothing; and picking anything else folds
      // it, leaving the focus where that pick put it.
      await user.click(box());
      await user.click(await screen.findByRole("button", { name: /^deps/ }));
      expect(onSelect).toHaveBeenLastCalledWith({ step: "fix_loop", task: "mend.deps" });
      expect(frame()).not.toBeNull();
      await user.click(head());
      await user.click(btn(/^judge/));
      await waitFor(() => expect(frame()).toBeNull());
      expect(btn(/^judge/)).toHaveFocus();
    });

    it("opens a second step in place of an open one, and moves the focus to it", async () => {
      const user = userEvent.setup();
      const onExpand = vi.fn();
      const tasks = [{ id: "mend.fmt", label: "fmt" }, { id: "mend.deps", label: "deps" }, { id: "push.sync", label: "sync" }, { id: "push.tell", label: "tell" }];
      render(<Held loop={{ label: "round 1", tasks }} onExpand={onExpand} />);
      // A double-click on a box selects it and expands the pane on the step, as it does on a task.
      await user.dblClick(box());
      expect(onExpand).toHaveBeenLastCalledWith({ step: "fix_loop", task: "mend" });
      await waitFor(() => expect(head()).toHaveFocus());
      await user.click(box("push"));
      await waitFor(() => expect(head("push")).toHaveFocus());
      // The first is its box again.
      await waitFor(() => expect(frame("mend")).toBeNull());
      expect(box()).toBeInTheDocument();
      // So does ⌘Enter on a head.
      await user.keyboard("{Meta>}{Enter}{/Meta}");
      expect(onExpand).toHaveBeenLastCalledWith({ step: "fix_loop", task: "push" });
    });

    it.each([
      ["a task waiting on a person is not read as running", [{ state: "done" as const }, { state: "current" as const, meta: "needs you" }], "needs you", false],
      ["a paused one says so and wears the pause badge", [{ state: "done" as const }, { state: "current" as const, paused: true, meta: "paused" }], "paused", true],
      ["one done and one not begun says how far", [{ state: "done" as const }, { state: "todo" as const }], "1 of 2 done", false],
      ["a failed one outranks a running one", [{ state: "current" as const, running: true }, { state: "failed" as const }], "failed", false],
    ])("names a closed loop step by its tasks: %s", (_, looks, word, paused) => {
      render(<NodeGraph name="v" steps={steps} loop={{ label: "round 1", tasks: looks.map((l, i) => ({ id: `mend.t${i}`, ...l })) }} />);
      expect(box()).toHaveAccessibleName(`mend, fix-loop step of 2 parallel tasks, ${word}`);
      expect(box().querySelector(".glyph-paused") !== null).toBe(paused);
    });

    it.each([["the step itself", "mend", /^mend,/], ["one of its tasks", "mend.deps", /^deps/]])("is open at once when the selection is %s", (_, task, pressed) => {
      render(<NodeGraph name="v" steps={steps} loop={parallel} selected={{ step: "fix_loop", task }} />);
      expect(btn(pressed)).toHaveAttribute("aria-pressed", "true");
      noBox();
    });

    it("draws no picker without rounds", () => {
      render(<NodeGraph name="v" steps={steps} loop={loop} />);
      expect(screen.queryByRole("button", { name: /round \d/ })).toBeNull();
    });
  });
});
