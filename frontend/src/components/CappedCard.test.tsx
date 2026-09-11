import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { CappedCard } from "./CappedCard";
import type { KraftEvent, WorkItem, WorkerSession } from "../types";

const item = {
  id: "w1",
  current_node_id: "verify",
  cappedOut: { cycles: 3, attempts: 3 },
  chain_definition: {
    template_id: "t",
    nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" }],
  },
} as WorkItem;

const session = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "capped_out", attempt: 1, round: 1, created_at: "t", started_at: null,
    exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null,
    model: null, ...over,
  }) as WorkerSession;

const ev = (over: Partial<KraftEvent>): KraftEvent =>
  ({ seq: 1, work_item_id: "w1", type: "node_started", payload: {}, created_at: "2026-09-04T10:00:00Z", ...over }) as KraftEvent;

const events: KraftEvent[] = [
  ev({
    seq: 1,
    type: "fix_cycle_started",
    payload: { node_id: "verify", cycle: 1, failed_tasks: ["on.test.run"] },
    created_at: "2026-09-04T10:00:00Z",
  }),
  ev({
    seq: 2,
    type: "fix_cycle_started",
    payload: { node_id: "verify", cycle: 2, failed_tasks: ["on.test.run"] },
    created_at: "2026-09-04T10:20:00Z",
  }),
  ev({
    seq: 3,
    type: "work_item_needs_human",
    payload: { node_id: "verify", reason: "cap" },
    created_at: "2026-09-04T11:12:00Z",
  }),
];

// fix_cycle_started{cycle: n} reports what the round n-1 measurement found
const sessions = [session({ id: "c0", round: 0 }), session({ id: "c1", round: 1 })];

beforeEach(() => vi.restoreAllMocks());

const renderCard = () =>
  render(<CappedCard item={item} sessions={sessions} events={events} />);

describe("CappedCard", () => {
  it("names the loop, its cap and how long it ran", () => {
    renderCard();
    expect(screen.getByText(/verify_fix_loop hit its cap/)).toBeInTheDocument();
    expect(screen.getByText(/3 attempts/)).toBeInTheDocument();
    expect(screen.getByText(/1h 12m/)).toBeInTheDocument();
    expect(screen.getByText(/on\.test\.run never went clean/)).toBeInTheDocument();
  });

  it("calls a crash a crash, not a loop that needs a steer (Kraft-esc)", () => {
    render(
      <CappedCard
        item={
          {
            ...item,
            cappedOut: undefined,
            stop_reason: "executor crashed: RuntimeError('boom')",
          } as WorkItem
        }
        sessions={sessions}
        events={events}
      />,
    );
    expect(screen.getByText(/Kraft crashed while running this node/)).toBeInTheDocument();
    expect(screen.queryByText(/needs a steer/)).toBeNull();
    expect(screen.getByText(/RuntimeError/)).toBeInTheDocument();
    // still the one way forward, crash or not
    expect(screen.getByRole("button", { name: /steer and retry/i })).toBeInTheDocument();
  });

  it("traces every cycle and links each to the session that measured it", async () => {
    renderCard();
    const rows = screen.getAllByText(/^cycle \d$/);
    expect(rows).toHaveLength(2);
    // opening a cycle's log opens the modal for that cycle's session
    const lines = vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "c0",
      status: "capped_out",
      lines: [],
    });
    await userEvent.click(screen.getAllByRole("button", { name: "log" })[0]);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    // cycle 1's row links to the session that *measured* the failure, round 0
    expect(lines).toHaveBeenCalledWith("c0");
  });

  it("still links the log when a cycle opened on findings alone (Kraft-jyh4)", async () => {
    // A review task that exits clean but reports an eligible finding opens a
    // cycle with no failed hook point at all -- `failed_tasks` is empty.
    const findingsEvents = [
      ev({
        seq: 1,
        type: "fix_cycle_started",
        payload: { node_id: "verify", cycle: 1, failed_tasks: [] },
        created_at: "2026-09-04T10:00:00Z",
      }),
      ev({
        seq: 3,
        type: "work_item_needs_human",
        payload: { node_id: "verify", reason: "cap" },
        created_at: "2026-09-04T11:12:00Z",
      }),
    ];
    render(<CappedCard item={item} sessions={sessions} events={findingsEvents} />);
    expect(screen.getByText("findings only")).toBeInTheDocument();
    const lines = vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "c0",
      status: "capped_out",
      lines: [],
    });
    await userEvent.click(screen.getByRole("button", { name: "log" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(lines).toHaveBeenCalledWith("c0");
  });

  it("sends the steer with the retry and clears the box", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({
      id: "w1", node_id: "verify", loop: "verify_fix_loop", steer: "the sign is flipped",
    });
    renderCard();
    const box = screen.getByLabelText(/Steer/);
    await userEvent.type(box, "the sign is flipped");
    await userEvent.click(screen.getByRole("button", { name: /steer and retry/i }));
    expect(spy).toHaveBeenCalledWith("w1", "the sign is flipped");
    expect(box).toHaveValue("");
  });

  it("retries with no steer at all when the box is empty", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({
      id: "w1", node_id: "verify", loop: "verify_fix_loop", steer: null,
    });
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: /steer and retry/i }));
    expect(spy).toHaveBeenCalledWith("w1", undefined);
  });

  it("surfaces a failed retry instead of silently doing nothing", async () => {
    vi.spyOn(api, "retryWorkItem").mockRejectedValue(new Error("node has no fix loop"));
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: /steer and retry/i }));
    expect(await screen.findByText(/no fix loop/)).toHaveClass("form-error");
  });

  it("offers escalate alongside steer and retry", async () => {
    const spy = vi.spyOn(api, "escalateWorkItem").mockResolvedValue({ id: "w1", status: "escalating" });
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: /^escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "what's flaky here?");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(spy).toHaveBeenCalledWith("w1", "what's flaky here?");
  });

  it("offers Skip alongside Retry", () => {
    renderCard();
    expect(screen.getByRole("button", { name: /skip/i })).toBeInTheDocument();
  });

  it("drops the steer box on a node with no agent task (Kraft-bz9b)", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({
      id: "w1", node_id: "open_mr", loop: "", steer: null,
    });
    render(
      <CappedCard
        item={{ ...item, current_node_id: "open_mr", cappedOut: undefined, steerable: false } as WorkItem}
        sessions={sessions}
        events={events}
      />,
    );
    expect(screen.queryByLabelText(/Steer/)).toBeNull();
    expect(screen.getByText(/this node has no agent to steer/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /^retry$/i });
    await userEvent.click(button);
    expect(spy).toHaveBeenCalledWith("w1", undefined);
  });

  it("drops its own retry hint and disables retry while an escalation is running", () => {
    const escalationSessions = [...sessions, session({ id: "e1", hook_point: "escalation", status: "running" })];
    render(<CappedCard item={item} sessions={escalationSessions} events={events} />);
    expect(screen.queryByText(/retry resets the loop counter/)).toBeNull();
    expect(screen.getByText(/Kraft agent is on it/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /steer and retry/i })).toBeDisabled();
  });
});
