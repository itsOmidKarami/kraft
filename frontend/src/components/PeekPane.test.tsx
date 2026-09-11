import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkerSession, WorkItem } from "../types";
import { PeekPane } from "./PeekPane";

const baseItem = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    title: "t",
    repo: "/r",
    status: "active",
    chain_template: "quick-task",
    chain_definition: {
      template_id: "quick-task",
      nodes: [
        { id: "plan", tasks: ["a"], gate_after: "plan_approval" },
        { id: "verify", tasks: ["b"], gate_after: null },
      ],
    },
    current_node_id: "verify",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

const setOneItem = (over: Partial<WorkItem> = {}) =>
  useStore.setState({ workItems: { w1: baseItem(over) } } as never);

const setSessions = (id: string, sessions: WorkerSession[]) =>
  useStore.setState((s) => ({ sessionsByItem: { ...s.sessionsByItem, [id]: sessions } }) as never);

beforeEach(() => {
  useStore.setState({
    workItems: {},
    sessionsByItem: {},
    eventsByItem: {},
  } as never);
  vi.restoreAllMocks();
  vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue();
});

describe("PeekPane", () => {
  it("renders the gate card inline when the item is at a gate", () => {
    setOneItem({ status: "needs_human", pending_gate: "plan_approval", current_node_id: "plan" });
    render(
      <MemoryRouter>
        <PeekPane id="w1" onClose={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/approve the plan/i)).toBeInTheDocument();
  });

  it("Escape calls onClose", async () => {
    setOneItem();
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the last 4 log lines of the current node's session", async () => {
    vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1",
      status: "running",
      lines: [
        { n: 1, t: null, src: "sys", text: "a" },
        { n: 2, t: null, src: "sys", text: "b" },
        { n: 3, t: null, src: "sys", text: "c" },
        { n: 4, t: null, src: "sys", text: "d" },
        { n: 5, t: null, src: "sys", text: "e" },
      ],
    });
    setOneItem({ current_node_id: "verify" });
    setSessions("w1", [
      { id: "s1", node_id: "verify", hook_point: "on.test.run", status: "running" } as never,
    ]);
    render(
      <MemoryRouter>
        <PeekPane id="w1" onClose={vi.fn()} />
      </MemoryRouter>,
    );
    expect(await screen.findByText("b")).toBeInTheDocument();
    expect(screen.queryByText("a")).toBeNull();
  });
});
