import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChainNode, WorkerSession, WorkItem } from "../../../types";
import { Tasks } from "./Tasks";

const NODES: ChainNode[] = [
  { id: "verify", tasks: ["on.test.run", "on.review.local.run"], gate_after: null },
  { id: "merge", tasks: ["on.merge"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1", title: "T", repo: "/r", status: "active", chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    effective_chain: { template_id: "default", nodes: NODES },
    current_node_id: "verify", bead_id: "B", created_at: "t", updated_at: "t",
    ...over,
  }) as WorkItem;

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "done", attempt: 1, thread: 1, round: 0,
    created_at: "2026-09-18T10:00:00", started_at: null, exited_at: null,
    tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null,
    ...over,
  }) as WorkerSession;

function renderTasks(over: { sessions?: WorkerSession[]; nodeId?: string | null } = {}) {
  return render(
    <Tasks
      item={item()}
      sessions={over.sessions ?? []}
      events={[]}
      nodeId={over.nodeId === undefined ? "verify" : over.nodeId}
      selected={null}
      onSelect={() => {}}
      scope="node"
      onScope={() => {}}
    />,
  );
}

describe("Tasks tab · a node's own tasks (Kraft-04fmo)", () => {
  it("lists every task of a node that has not run yet", () => {
    renderTasks({ sessions: [] });
    const list = document.querySelector('[data-testid="node-task-list"]');
    expect(list).toBeTruthy();
    expect(document.querySelector('[data-testid="node-task-on.test.run"]')).toBeTruthy();
    expect(
      document.querySelector('[data-testid="node-task-on.review.local.run"]'),
    ).toBeTruthy();
  });

  it("marks a task with no session as not started", () => {
    renderTasks({ sessions: [] });
    const row = document.querySelector('[data-testid="node-task-on.test.run"]');
    expect(row?.textContent).toContain("not started");
  });

  it("shows a dispatched task's status and leaves its sibling not started", () => {
    renderTasks({ sessions: [session({ status: "running" })] });
    expect(
      document.querySelector('[data-testid="node-task-on.test.run"]')?.textContent,
    ).toContain("running");
    expect(
      document.querySelector('[data-testid="node-task-on.review.local.run"]')?.textContent,
    ).toContain("not started");
  });

  it("shows the latest session's status when a task has several", () => {
    renderTasks({
      sessions: [
        session({ id: "old", status: "failed", created_at: "2026-09-18T10:00:00" }),
        session({ id: "new", status: "done", created_at: "2026-09-18T11:00:00" }),
      ],
    });
    const row = document.querySelector('[data-testid="node-task-on.test.run"]');
    expect(row?.textContent).toContain("done");
    expect(row?.textContent).not.toContain("failed");
  });

  it("says the tasks run concurrently", () => {
    renderTasks();
    expect(
      document.querySelector('[data-testid="node-task-list"]')?.parentElement?.textContent,
    ).toMatch(/concurrent/i);
  });

  it("renders no section when the selected node is not in the chain", () => {
    renderTasks({ nodeId: "nope" });
    expect(document.querySelector('[data-testid="node-task-list"]')).toBeNull();
  });

  it("renders no section when no node is selected", () => {
    renderTasks({ nodeId: null });
    expect(document.querySelector('[data-testid="node-task-list"]')).toBeNull();
  });

  it("groups a stepped node's tasks and does not call them all concurrent", () => {
    const stepped: ChainNode[] = [
      { id: "verify", tasks: ["on.a", "on.b", "on.c"],
        steps: [["on.a"], ["on.b", "on.c"]], gate_after: null },
    ];
    render(
      <Tasks
        item={{ ...item(), effective_chain: { template_id: "d", nodes: stepped },
                chain_definition: { template_id: "d", nodes: stepped } } as WorkItem}
        sessions={[]} events={[]} nodeId="verify" selected={null}
        onSelect={() => {}} scope="node" onScope={() => {}}
      />,
    );
    const list = document.querySelector('[data-testid="node-task-list"]');
    expect(list?.textContent ?? "").not.toMatch(/·\s*CONCURRENT/i);
    expect(document.querySelectorAll('[data-testid^="node-step-"]')).toHaveLength(2);
  });

  it("still labels a single-group node concurrent", () => {
    renderTasks();
    expect(
      document.querySelector('[data-testid="node-task-list"]')?.parentElement?.textContent,
    ).toMatch(/concurrent/i);
  });
});

describe("a session's run number (Kraft-clzjr)", () => {
  it("says run, not attempt: the number is a serial, not a retry count", () => {
    renderTasks({ sessions: [session({ attempt: 15 })] });
    const row = document.querySelector('[data-testid="task-row-s1"]');
    expect(row?.textContent).toContain("run 15");
    expect(row?.textContent).not.toContain("attempt");
  });

  it("still says turn N on an escalation row", () => {
    renderTasks({ sessions: [session({ hook_point: "escalation", attempt: 2 })] });
    expect(
      document.querySelector('[data-testid="task-row-s1"]')?.textContent,
    ).toContain("turn 2");
  });
});
