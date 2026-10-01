import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodeGraph } from "./NodeGraph";
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
});
