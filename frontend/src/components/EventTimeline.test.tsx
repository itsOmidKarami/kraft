import { render, screen } from "@testing-library/react";
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
});
