import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Chip, MiniChain, OverflowMenu, RowState, StatusGlyph, Switch, Tabs } from "./ui";

const NODES = [
  { id: "env_setup", tasks: ["a"], gate_after: null },
  { id: "verify", tasks: ["a", "b"], gate_after: null, fix_loop: "verify_fix_loop" },
  { id: "merge", tasks: ["c"], gate_after: "human_review_approval" },
];

describe("MiniChain", () => {
  it("marks done, current and todo segments and ticks gated nodes", () => {
    render(<MiniChain nodes={NODES} currentNodeId="verify" done={["env_setup"]} size="lg" />);
    expect(screen.getByTestId("node-env_setup")).toHaveAttribute("data-state", "done");
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "current");
    expect(screen.getByTestId("node-merge")).toHaveAttribute("data-state", "todo");
    expect(screen.getByTitle("human_review_approval")).toBeInTheDocument();
  });

  it("labels segments only at lg; sm falls back to a title tooltip", () => {
    const { unmount } = render(<MiniChain nodes={NODES} currentNodeId="verify" size="lg" />);
    expect(screen.getByText("verify")).toBeInTheDocument();
    unmount();
    render(<MiniChain nodes={NODES} currentNodeId="verify" size="sm" />);
    expect(screen.queryByText("verify")).not.toBeInTheDocument();
    expect(screen.getByTitle("verify")).toBeInTheDocument();
  });

  it("drops the current segment to paused when the item is paused", () => {
    render(<MiniChain nodes={NODES} currentNodeId="verify" size="lg" paused />);
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "paused");
  });

  it("draws a node list with no work item behind it", () => {
    // The chain-template editor draws a draft that has no work item: nothing is
    // current, nothing is done, and every segment is todo.
    render(<MiniChain nodes={NODES} size="lg" />);
    expect(screen.getByTestId("node-env_setup")).toHaveAttribute("data-state", "todo");
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "todo");
    expect(screen.getByText("merge")).toBeInTheDocument();
  });

  it("marks an unresolved segment", () => {
    // Invalid outranks current: in the editor a node whose task does not
    // resolve is the thing to look at.
    render(<MiniChain nodes={NODES} currentNodeId="verify" invalid={["verify"]} size="lg" />);
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "invalid");
  });
});

describe("StatusGlyph / RowState", () => {
  it("carries the status on a data attribute so the tone is CSS, not a second hue", () => {
    render(
      <>
        <StatusGlyph status="running" />
        <RowState status="capped_out">capped 3/3</RowState>
      </>,
    );
    expect(screen.getByRole("img", { name: "running" })).toHaveAttribute("data-status", "running");
    expect(screen.getByText("capped 3/3")).toHaveAttribute("data-status", "capped_out");
  });

  it("has a glyph for rate_limited, not the unknown-status fallback", () => {
    render(<StatusGlyph status="rate_limited" />);
    expect(screen.getByRole("img", { name: "rate_limited" })).toBeInTheDocument();
  });

  it("has a glyph for waiting, not the unknown-status fallback", () => {
    render(<StatusGlyph status="waiting" />);
    expect(screen.getByRole("img", { name: "waiting" })).toBeInTheDocument();
  });

  it("renders waiting with the same icon as rate_limited", () => {
    // The label alone (above) can't tell a real glyph from the Question
    // fallback -- aria-label is set from the status string unconditionally,
    // whichever icon actually renders. Both statuses are the same shape (a
    // poller-driven wait, Kraft-ru98) and are meant to share a glyph; this
    // catches the glyph itself regressing to the fallback.
    const { container: waiting } = render(<StatusGlyph status="waiting" />);
    const { container: rateLimited } = render(<StatusGlyph status="rate_limited" />);
    expect(waiting.querySelector("svg")?.outerHTML).toBe(
      rateLimited.querySelector("svg")?.outerHTML,
    );
  });

  it("has a glyph for every item-level design state", () => {
    for (const status of [
      "gate",
      "capped",
      "question",
      "budget",
      "not_started",
      "abandoned",
    ] as const) {
      const { unmount } = render(<StatusGlyph status={status} />);
      expect(screen.getByRole("img", { name: status })).toBeInTheDocument();
      unmount();
    }
  });
});

describe("OverflowMenu", () => {
  it("keeps actions hidden until opened, then closes on select", async () => {
    const onSelect = vi.fn();
    render(<OverflowMenu items={[{ label: "Delete", onSelect, danger: true }]} />);
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("marks the selected tab and reports changes", async () => {
    const onChange = vi.fn();
    render(
      <Tabs
        tabs={[
          { id: "tasks", label: "Tasks", count: 2 },
          { id: "timeline", label: "Timeline" },
        ]}
        value="tasks"
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("tab", { name: /Tasks/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(onChange).toHaveBeenCalledWith("timeline");
  });
});

describe("Switch", () => {
  it("is a role=switch that reports its toggled value and carries a label", async () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Auto-intake" />);
    const el = screen.getByRole("switch", { name: "Auto-intake" });
    expect(el).toHaveAttribute("aria-checked", "false");
    await userEvent.click(el);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not fire when disabled", async () => {
    const onChange = vi.fn();
    render(<Switch checked={true} onChange={onChange} label="Auto-intake" disabled />);
    await userEvent.click(screen.getByRole("switch", { name: "Auto-intake" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Chip", () => {
  it("renders a count and reports selection via aria-pressed", async () => {
    const onClick = vi.fn();
    render(<Chip label="Needs you" count={3} selected onClick={onClick} />);
    const el = screen.getByRole("button", { name: /Needs you/ });
    expect(el).toHaveAttribute("aria-pressed", "true");
    expect(el).toHaveTextContent("3");
    await userEvent.click(el);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("defaults to unselected with no count shown", () => {
    render(<Chip label="Running" />);
    const el = screen.getByRole("button", { name: "Running" });
    expect(el).toHaveAttribute("aria-pressed", "false");
  });
});
