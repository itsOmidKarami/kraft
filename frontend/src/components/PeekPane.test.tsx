import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkerSession, WorkItem } from "../types";
import { PeekPane } from "./PeekPane";
import { item, QUICK } from "../testFixtures";

const baseItem = (over: Partial<WorkItem> = {}): WorkItem => item({ title: "t", ...QUICK, ...over });

const setOneItem = (over: Partial<WorkItem> = {}) =>
  useStore.setState({ workItems: { w1: baseItem(over) } } as never);

const setSessions = (id: string, sessions: WorkerSession[]) =>
  useStore.setState((s) => ({ sessionsByItem: { ...s.sessionsByItem, [id]: sessions } }) as never);

const renderPeek = () =>
  render(
    <MemoryRouter>
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
      <MemoryRouter>
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

  // W10.D: a reported build showed the raw 32-hex id and scrolled sideways. The
  // head is one grid -- id + meta | Open → | ✕ -- and the id is ShortId's.
  it("heads the pane with the short id, never the raw 32-hex, in id+meta | Open → | ✕ cells", () => {
    const id = "c7446dca30d840a8a69977c6649a7b11";
    useStore.setState({ workItems: { [id]: baseItem({ id, repo: "/Users/dev/code/kraft" }) } } as never);
    render(
      <MemoryRouter>
        <PeekPane id={id} onClose={vi.fn()} />
      </MemoryRouter>,
    );
    const head = document.querySelector(".peek-head") as HTMLElement;
    expect(head.textContent).not.toContain(id);
    expect(within(head).getByTitle(id).textContent).toBe("c7446dca…a7b11");
    const cells = [...head.children];
    expect(cells.map((c) => c.classList.contains("peek-head-id") || c.classList.contains("peek-open") || c.classList.contains("peek-close"))).toEqual([true, true, true]);
    expect(within(cells[0] as HTMLElement).getByText("kraft")).toBeTruthy();
  });

  it("renders the hero card with the current node and its task line when progress is set", () => {
    setOneItem({
      current_node_id: "verify",
      progress: { current: 3, total: 6, title: "open_mr refuses a dirty worktree" },
    });
    renderPeek();
    const hero = document.querySelector(".peek-hero") as HTMLElement;
    expect(within(hero).getByText("verify")).toBeTruthy();
    expect(within(hero).getByText("3 of 6")).toBeTruthy();
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

  it("shows the running session's log, not a 0s job that finished later", async () => {
    setOneItem({ current_node_id: "implementation" });
    setSessions("w1", [
      { id: "run", node_id: "implementation", status: "running", created_at: "2026-09-12T09:00:00Z" } as never,
      { id: "scan", node_id: "implementation", status: "done", created_at: "2026-09-12T09:00:01Z" } as never,
    ]);
    const spy = vi.spyOn(api, "getLogLines").mockResolvedValue({ session_id: "run", status: "running", lines: [] });
    renderPeek();
    await waitFor(() => expect(spy).toHaveBeenCalledWith("run"));
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
      <MemoryRouter>
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
      <MemoryRouter>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("heading", { level: 2 }));
    expect(onClose).not.toHaveBeenCalled();
  });

  // Kraft-absw: the merge request is one click from the board.
  it("links the MR beside Open → once mr_ref is set", () => {
    setOneItem({ mr_ref: { number: 142, url: "https://example.test/mr/142" } });
    renderPeek();
    const link = screen.getByRole("link", { name: /MR !142/ });
    expect(link).toHaveAttribute("href", "https://example.test/mr/142");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("shows no MR link when mr_ref is absent", () => {
    setOneItem();
    renderPeek();
    expect(screen.queryByRole("link", { name: /MR/ })).toBeNull();
  });

  // Kraft-av3t: the card is selected from the same `deriveState` the header
  // tag reads, so a stranded needs_human stop (no cappedOut, no pending_gate)
  // can't show "capped" in the header with a card that refuses to act.
  it("renders the item page's card, narrow: no stats line, no document link, Open item → first in More (W11 · I)", async () => {
    setOneItem({ status: "needs_human", pending_gate: "plan_approval", current_node_id: "plan", gate_artifact: "docs/plan.md" });
    renderPeek();
    const card = document.querySelector(".peek-pane .item-card") as HTMLElement;
    expect(card).toBeTruthy();
    expect(card.querySelector(".item-card-stats")).toBeNull();
    expect(within(card).queryByRole("link", { name: /review plan|read document/i })).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem")[0]).toHaveTextContent("Open item →");
  });

  it.each<[string, Partial<WorkItem>, string[]]>([
    ["gate", { status: "needs_human", pending_gate: "plan_approval", current_node_id: "plan" }, ["Approve", "Reject"]],
    ["running", { status: "active" }, ["Pause"]],
    ["paused", { status: "paused" }, ["Resume", "Steer"]],
    ["capped", { status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }, ["Steer & retry", "Escalate"]],
    ["budget", { status: "needs_human", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } }, ["Raise budget", "Escalate"]],
    ["question", { status: "needs_human", needs_context_question: "which?" }, ["Answer"]],
    ["done", { status: "completed", mr_ref: { number: 1, url: "https://x" } }, ["Open MR"]],
  ])("%s: the peek's button row is the state's set", (_, over, want) => {
    setOneItem(over);
    renderPeek();
    const row = document.querySelector(".peek-pane .item-card-actions") as HTMLElement;
    expect([...row.querySelectorAll(":scope > button, :scope > a")].map((b) => b.textContent?.trim())).toEqual(want);
  });

  it("opens on the composer a board row's button asked for, inside the peek (W11 · B.3, I.3)", () => {
    setOneItem({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } });
    render(
      <MemoryRouter>
        <PeekPane id="w1" onClose={vi.fn()} compose="steerRetry" />
      </MemoryRouter>,
    );
    const pane = screen.getByLabelText("peek");
    expect(within(pane).getByLabelText(/composer message/i)).toBeInTheDocument();
  });

  it("Escape in More actions closes the menu, not the peek", async () => {
    const onClose = vi.fn();
    setOneItem({ status: "active" });
    render(
      <MemoryRouter>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("renders the card's Start button for a created-but-never-started item", () => {
    // paused with no current_node_id derives to "not_started" (Kraft-av3t);
    // it must still get PausedCard's neverStarted branch, not a blank pane.
    setOneItem({ status: "paused", current_node_id: null });
    renderPeek();
    expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
  });

  it("shows the first node, not the last, in the hero for a created-but-never-started item", () => {
    // A null current_node_id also means "finished" for a done/archived item,
    // where the last node is the right fallback -- but for a paused item
    // that never ran, null means "hasn't reached node 1 yet", not "ran off
    // the end of the chain" (Kraft-3j02r).
    setOneItem({ status: "paused", current_node_id: null });
    renderPeek();
    const hero = document.querySelector(".peek-hero") as HTMLElement;
    expect(within(hero).getByText("plan")).toBeTruthy();
    expect(within(hero).getByText(/node 1 of 2/)).toBeTruthy();
  });

  it("renders the capped card, not a refusal, for a stranded needs_human stop", () => {
    setOneItem({ status: "needs_human" });
    renderPeek();
    expect(document.querySelector(".tag-outline")?.textContent).toBe("capped");
    expect(screen.queryByText(/cannot make yet/i)).toBeNull();
  });

  it("renders the escalated card, not the generic refusal, once a turn has reported", () => {
    setOneItem({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } });
    useStore.setState((s) => ({
      eventsByItem: {
        ...s.eventsByItem,
        w1: [
          { seq: 1, work_item_id: "w1", type: "work_item_needs_human", payload: {}, created_at: "2026-01-01T00:00:00Z" },
          { seq: 2, work_item_id: "w1", type: "escalation_message", payload: { session_id: "e1", message: "go" }, created_at: "2026-01-01T00:05:00Z" },
        ],
      },
    }) as never);
    setSessions("w1", [
      {
        id: "e1",
        node_id: "verify",
        hook_point: "escalation",
        status: "done",
        attempt: 1,
        created_at: "2026-01-01T00:05:00Z",
      } as never,
    ]);
    renderPeek();
    expect(document.querySelector(".tag-outline")?.textContent).toBe("escalated");
    expect(screen.getByTestId("escalated-card")).toBeInTheDocument();
    expect(screen.queryByText(/cannot make yet/i)).toBeNull();
  });

  it("renders the escalating pill, not a demand, while a turn is running", () => {
    setOneItem({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } });
    useStore.setState((s) => ({
      eventsByItem: {
        ...s.eventsByItem,
        w1: [
          { seq: 1, work_item_id: "w1", type: "work_item_needs_human", payload: {}, created_at: "2026-01-01T00:00:00Z" },
          { seq: 2, work_item_id: "w1", type: "escalation_message", payload: { session_id: "e1", message: "go" }, created_at: "2026-01-01T00:05:00Z" },
        ],
      },
    }) as never);
    setSessions("w1", [
      {
        id: "e1",
        node_id: "verify",
        hook_point: "escalation",
        status: "running",
        attempt: 1,
        created_at: "2026-01-01T00:05:00Z",
      } as never,
    ]);
    renderPeek();
    expect(document.querySelector(".tag-outline")?.textContent).toBe("escalating");
    expect(screen.getByTestId("escalating-pill")).toBeInTheDocument();
    expect(screen.queryByText(/cannot make yet/i)).toBeNull();
  });

  it("never renders raw agent-event JSON in the log, falling back to its type", async () => {
    vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1",
      status: "running",
      lines: [
        { n: 1, t: null, src: "agent", text: '{"type":"tool_progress","tool_use_id":"toolu_01"}' },
      ],
    });
    setOneItem({ current_node_id: "verify" });
    setSessions("w1", [
      { id: "s1", node_id: "verify", hook_point: "on.test.run", status: "running" } as never,
    ]);
    renderPeek();
    expect(await screen.findByText("tool_progress")).toBeInTheDocument();
    expect(screen.queryByText(/toolu_01/)).toBeNull();
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
      <MemoryRouter>
        <PeekPane id="w1" onClose={onClose} />
      </MemoryRouter>,
    );
    await user.click(row);
    expect(onClose).not.toHaveBeenCalled();
    row.remove();
  });
});
