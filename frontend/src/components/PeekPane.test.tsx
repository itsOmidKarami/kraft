import { render, screen, within } from "@testing-library/react";
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

const renderPeek = () =>
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <PeekPane id="w1" onClose={vi.fn()} />
    </MemoryRouter>,
  );

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

  it("renders a scrim that closes the peek when clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    setOneItem();
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(document.querySelector(".peek-scrim") as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the id row with Open → and a close button, and no full-width Open button", () => {
    setOneItem();
    renderPeek();
    expect(screen.getByRole("link", { name: /Open/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /close/i })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /Open/ })).toHaveLength(1);
    expect(screen.queryByText(/updated .* ago/)).toBeNull();
  });

  it("renders the hero card with the current node and its task line when progress is set", () => {
    setOneItem({
      current_node_id: "verify",
      progress: { current: 3, total: 6, title: "open_mr refuses a dirty worktree" },
    });
    renderPeek();
    const hero = document.querySelector(".peek-hero") as HTMLElement;
    expect(within(hero).getByText("verify")).toBeTruthy();
    expect(within(hero).getByText("Task 3 of 6")).toBeTruthy();
    expect(within(hero).getByTestId("task-bar")).toBeTruthy();
  });

  it("compresses the stage list to done / current / remaining", () => {
    setOneItem({ current_node_id: "verify", completedNodes: ["plan"] });
    renderPeek();
    const rows = document.querySelectorAll(".peek-stage");
    expect(rows).toHaveLength(2); // plan done, verify current, nothing remaining
    expect(rows[0].textContent).toContain("plan");
    expect(rows[0].textContent).toContain("done");
    expect(rows[1].textContent).toContain("verify");
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

  // The scrim is display:none above 1280 (the pane sits beside clickable
  // rows), so on a wide screen clicking away had no target at all and only
  // Esc closed the peek.
  it("closes when the click lands outside the pane", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    setOneItem();
    const outside = document.createElement("button");
    outside.textContent = "elsewhere";
    document.body.appendChild(outside);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(outside);
    expect(onClose).toHaveBeenCalled();
    outside.remove();
  });

  it("does not close when the click lands on the pane itself", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    setOneItem();
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("heading", { level: 2 }));
    expect(onClose).not.toHaveBeenCalled();
  });

  // Another row switches the peek to that item (Board's own onSelect); if
  // this listener treated a row as "outside", the peek would close on the
  // pointerdown and reopen on the click.
  it("does not close when the click lands on a board row", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    setOneItem();
    const row = document.createElement("div");
    row.className = "board-row";
    row.textContent = "another item";
    document.body.appendChild(row);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(row);
    expect(onClose).not.toHaveBeenCalled();
    row.remove();
  });
});
