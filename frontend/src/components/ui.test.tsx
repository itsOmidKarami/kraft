import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChainBar, OverflowMenu, RowState, StatusGlyph, Tabs } from "./ui";
import type { WorkItem } from "../types";

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    current_node_id: "verify",
    completedNodes: ["env_setup"],
    chain_definition: {
      template_id: "t",
      nodes: [
        { id: "env_setup", tasks: ["a"], gate_after: null },
        { id: "verify", tasks: ["a", "b"], gate_after: null, fix_loop: "verify_fix_loop" },
        { id: "merge", tasks: ["c"], gate_after: "human_review_approval" },
      ],
    },
    ...over,
  }) as WorkItem;

describe("ChainBar", () => {
  it("marks done, current and todo segments and ticks gated nodes", () => {
    render(<ChainBar item={item()} size="lg" />);
    expect(screen.getByTestId("node-env_setup")).toHaveAttribute("data-state", "done");
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "current");
    expect(screen.getByTestId("node-merge")).toHaveAttribute("data-state", "todo");
    expect(screen.getByTitle("human_review_approval")).toBeInTheDocument();
  });

  it("labels segments only at lg; sm falls back to a title tooltip", () => {
    const { unmount } = render(<ChainBar item={item()} size="lg" />);
    expect(screen.getByText("verify")).toBeInTheDocument();
    unmount();
    render(<ChainBar item={item()} size="sm" />);
    expect(screen.queryByText("verify")).not.toBeInTheDocument();
    expect(screen.getByTitle("verify")).toBeInTheDocument();
  });

  it("drops the current segment to paused when the item is paused", () => {
    render(<ChainBar item={item()} size="lg" paused />);
    expect(screen.getByTestId("node-verify")).toHaveAttribute("data-state", "paused");
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
