import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkItem, WorkerSession } from "../types";
import { CurrentNodePanel } from "./CurrentNodePanel";

const item = {
  id: "w1",
  current_node_id: "verify",
  fixCycle: 2,
  chain_definition: {
    template_id: "t",
    nodes: [
      { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
      { id: "implementation", tasks: ["on.implementation.start"], gate_after: null },
      { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
    ],
  },
} as WorkItem;

const s = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, created_at: "t", exited_at: null, ...over,
  }) as WorkerSession;

describe("CurrentNodePanel", () => {
  it("renders a chip per current-node session", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "a", status: "running" }), s({ id: "b", status: "capped_out" })]} />);
    expect(screen.getByTestId("session-a")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("session-b")).toHaveAttribute("data-status", "capped_out");
  });

  it("badges a fix task row", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "f", hook_point: "on.implementation.start" })]} />);
    expect(screen.getByTestId("session-f")).toHaveTextContent("fix · cycle 2");
  });

  it("groups every session by node in chain order, current node expanded", () => {
    // Kraft-n9gw: the tab filtered to the current node, so it was one row —
    // and between nodes it read "no tasks running on this node" while a dozen
    // sessions sat in the payload.
    const { container } = render(
      <CurrentNodePanel
        item={item}
        sessions={[
          s({ id: "v", node_id: "verify", hook_point: "on.test.run", status: "running" }),
          s({
            id: "p",
            node_id: "plan",
            hook_point: "on.plan.requested",
            status: "done",
            tokens_in: 40_000,
            tokens_out: 1_200,
            cost_usd: 1.1,
            wall_ms: 272_309,
          }),
          s({ id: "i", node_id: "implementation", hook_point: "on.implementation.start", status: "done" }),
        ]}
      />,
    );

    const groups = [...container.querySelectorAll("details[data-node]")];
    expect(groups.map((g) => g.getAttribute("data-node"))).toEqual([
      "plan",
      "implementation",
      "verify",
    ]);
    // only the current node arrives open
    expect(groups[0]).not.toHaveAttribute("open");
    expect(groups[1]).not.toHaveAttribute("open");
    expect(groups[2]).toHaveAttribute("open");

    // a finished node's log is reachable — it was not, before this
    expect(screen.getByTestId("session-p")).toHaveTextContent("view log");
    // and its usage sits where the numbers are
    expect(screen.getByTestId("session-p")).toHaveTextContent("41.2k tokens");
    expect(screen.getByTestId("session-p")).toHaveTextContent("$1.10");
    expect(screen.getByTestId("session-p")).toHaveTextContent("4m");
  });

  it("opens the most recent group when the current node has no session yet", () => {
    // Between two nodes — or once the chain is done — current_node_id names a
    // node nothing has run in. Falling through to "nothing open" would show
    // every group collapsed with no row visible anywhere (the regression this
    // pins: e2e/lifecycle.spec.ts's deep-link reload hit it directly).
    const between = { ...item, current_node_id: "open_mr" } as WorkItem;
    const { container } = render(
      <CurrentNodePanel
        item={between}
        sessions={[
          s({ id: "p", node_id: "plan", hook_point: "on.plan.requested", status: "done" }),
          s({ id: "v", node_id: "verify", hook_point: "on.test.run", status: "done" }),
        ]}
      />,
    );

    const groups = [...container.querySelectorAll("details[data-node]")];
    expect(groups.map((g) => g.getAttribute("data-node"))).toEqual(["plan", "verify"]);
    expect(groups[0]).not.toHaveAttribute("open");
    expect(groups[1]).toHaveAttribute("open");
    expect(screen.getByTestId("session-v")).toBeVisible();
  });
});
