import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodeGraph } from "./NodeGraph";
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

    it("draws no picker without rounds", () => {
      render(<NodeGraph name="v" steps={steps} loop={loop} />);
      expect(screen.queryByRole("button", { name: /round \d/ })).toBeNull();
    });
  });
});
