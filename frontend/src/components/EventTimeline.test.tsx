import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../types";
import { EventTimeline } from "./EventTimeline";

const ev = (over: Partial<KraftEvent>): KraftEvent =>
  ({
    seq: 1,
    work_item_id: "w1",
    type: "node_started",
    payload: {},
    created_at: "2026-09-04T00:00:00Z",
    ...over,
  }) as KraftEvent;

describe("EventTimeline", () => {
  it("surfaces the rejection note", () => {
    render(
      <EventTimeline
        events={[ev({ type: "gate_rejected", payload: { gate: "spec_approval", note: "too vague" } })]}
      />,
    );
    expect(screen.getByText("too vague")).toBeInTheDocument();
  });

  it("surfaces the needs_human reason", () => {
    render(
      <EventTimeline
        events={[
          ev({ type: "work_item_needs_human", payload: { node_id: "verify", reason: "cap reached" } }),
        ]}
      />,
    );
    expect(screen.getByText("cap reached")).toBeInTheDocument();
  });

  it("surfaces a worker's concerns, the one surface every chain has", () => {
    // The gate panel is the only other place concerns render, so on a chain
    // with no review gate they were stored and shown nowhere.
    render(
      <EventTimeline
        events={[
          ev({
            type: "worker_session_exited",
            payload: { status: "done_with_concerns", concerns: "the migration is untested" },
          }),
        ]}
      />,
    );
    expect(screen.getByText("the migration is untested")).toBeInTheDocument();
  });

  it("surfaces the failed tasks that opened a fix cycle", () => {
    render(
      <EventTimeline
        events={[
          ev({
            type: "fix_cycle_started",
            payload: { node_id: "verify", cycle: 2, failed_tasks: ["on.test.run"] },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/cycle 2/)).toBeInTheDocument();
    expect(screen.getByText(/on\.test\.run/)).toBeInTheDocument();
  });

  it("renders nothing extra for an event with no detail", () => {
    const { container } = render(<EventTimeline events={[ev({ type: "node_started" })]} />);
    expect(container.querySelector(".event-detail")).toBeNull();
  });

  it("groups events under the node they happened on, newest node first", () => {
    const { container } = render(
      <EventTimeline
        events={[
          ev({ seq: 1, type: "node_started", payload: { node_id: "plan" }, created_at: "2026-09-04T10:00:00Z" }),
          ev({ seq: 2, type: "gate_approved", payload: { gate: "plan_approval" }, created_at: "2026-09-04T10:05:00Z" }),
          ev({ seq: 3, type: "node_started", payload: { node_id: "verify" }, created_at: "2026-09-04T10:06:00Z" }),
        ]}
      />,
    );
    const groups = [...container.querySelectorAll(".timeline-group")];
    expect(groups.map((g) => g.getAttribute("data-node"))).toEqual(["verify", "plan"]);
    // gate_approved carries no node_id — it belongs to the node that was running
    expect(within(groups[1] as HTMLElement).getByText("gate_approved")).toBeInTheDocument();
  });

  it("orders events newest first inside a group and leads the live group in accent", () => {
    const { container } = render(
      <EventTimeline
        events={[
          ev({ seq: 1, type: "node_started", payload: { node_id: "verify" } }),
          ev({ seq: 2, type: "fix_cycle_started", payload: { node_id: "verify", cycle: 2, failed_tasks: ["t"] } }),
        ]}
      />,
    );
    const rows = [...container.querySelectorAll(".event-row")];
    expect(rows.map((r) => r.getAttribute("data-type"))).toEqual([
      "fix_cycle_started",
      "node_started",
    ]);
    expect(container.querySelector(".event-dot")).toHaveAttribute("data-age", "0");
  });

  it("offers a log link only on worker-session events", () => {
    render(
      <EventTimeline
        events={[
          ev({ seq: 1, type: "node_started", payload: { node_id: "verify" } }),
          ev({ seq: 2, type: "worker_session_started", payload: { node_id: "verify", session_id: "s1" } }),
        ]}
      />,
    );
    expect(screen.getAllByRole("button", { name: "view log" })).toHaveLength(1);
  });
});
