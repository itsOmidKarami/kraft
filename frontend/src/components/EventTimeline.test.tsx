import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { KraftEvent, WorkerSession } from "../types";
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
    expect(screen.getByText(/the migration is untested/)).toBeInTheDocument();
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

  it("surfaces why a straggler-commit sweep failed (Kraft-hf12)", () => {
    render(
      <EventTimeline
        events={[
          ev({
            type: "sweep_failed",
            payload: {
              node_id: "verify",
              task_hook: "on.implementation.start",
              error: "git commit failed: .git/index.lock exists",
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/on\.implementation\.start/)).toBeInTheDocument();
    expect(screen.getByText(/index\.lock/)).toBeInTheDocument();
  });

  it("says what a node's repair pass is repairing", () => {
    // The repair re-runs the node's own tasks, so the timeline would otherwise
    // show the same measurement twice with nothing to explain it (Kraft-rv6i).
    render(
      <EventTimeline
        events={[
          ev({
            type: "node_recovery_started",
            payload: {
              node_id: "mr_checks",
              failed_tasks: ["on.ci.poll"],
              tasks: ["on.mr.remediate"],
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/on\.ci\.poll failed → on\.mr\.remediate/)).toBeInTheDocument();
  });

  it("says which webhook failed and why, not just that one did", () => {
    // The point of the event is diagnosis: a dead webhook has to be
    // distinguishable from a quiet one (Kraft-8mu.11).
    const { rerender } = render(
      <EventTimeline
        events={[
          ev({
            type: "notification_failed",
            payload: {
              event_type: "gate_requested",
              status: 502,
              host: "hooks.example.com",
              error: null,
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/hooks\.example\.com/)).toHaveTextContent("gate_requested");
    expect(screen.getByText(/hooks\.example\.com/)).toHaveTextContent("502");

    // never reached the host: no status, an exception name instead
    rerender(
      <EventTimeline
        events={[
          ev({
            type: "notification_failed",
            payload: {
              event_type: "gate_requested",
              status: null,
              host: "hooks.example.com",
              error: "ConnectTimeout",
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/hooks\.example\.com/)).toHaveTextContent("ConnectTimeout");
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

  it("offers a log link on any event that names a session", () => {
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

  it("offers a log link on work_item_needs_human, where a human actually lands", () => {
    // Kraft-eh6p: the reason names the hook, not the failure. The failure is in
    // the session's log, and this button is the only route to it that does not
    // require noticing the worker_session_exited row above.
    render(
      <EventTimeline
        events={[
          ev({
            seq: 1,
            type: "work_item_needs_human",
            payload: { node_id: "open_mr", reason: "task failed in node open_mr: on.mr.open", session_id: "s9" },
          }),
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: "view log" })).toBeInTheDocument();
  });

  it("surfaces the retry time on a rate-limit stop", () => {
    render(
      <EventTimeline
        events={[
          ev({
            type: "work_item_rate_limited",
            payload: { node_id: "implementation", retry_at: "2026-09-10T05:00:00Z" },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/2026-09-10T05:00:00Z/)).toBeInTheDocument();
  });

  it("names a worker_session_exited row by hook_point resolved from sessions", () => {
    // Kraft-zxu4: a verify node read as eight identical grey rows. The exit
    // payload carries no hook_point at all, so it is resolved through the
    // sessions already in the detail payload rather than by widening the
    // event — widening it would fix only events written after the change and
    // leave every item already in the database unreadable.
    render(
      <EventTimeline
        sessions={[
          { id: "s-test", hook_point: "on.test.run" } as WorkerSession,
          { id: "s-rev", hook_point: "on.review.local.run" } as WorkerSession,
        ]}
        events={[
          ev({
            seq: 1,
            type: "worker_session_exited",
            payload: { session_id: "s-test", status: "done", wall_ms: 272_309 },
          }),
          ev({
            seq: 2,
            type: "worker_session_exited",
            payload: { session_id: "s-rev", status: "failed", wall_ms: 6 },
          }),
        ]}
      />,
    );
    expect(screen.getByText("on.test.run exited")).toBeInTheDocument();
    expect(screen.getByText("on.review.local.run exited")).toBeInTheDocument();
    // a done and a failed exit stop looking identical
    expect(screen.getByText("done · 4m")).toBeInTheDocument();
    expect(screen.getByText("failed · 0s")).toBeInTheDocument();
  });

  it("falls back to the raw type when no session resolves the row", () => {
    render(
      <EventTimeline
        events={[ev({ type: "worker_session_exited", payload: { session_id: "gone", status: "done" } })]}
      />,
    );
    expect(screen.getByText("worker_session_exited")).toHaveClass("etype");
  });

  it("says how many findings were measured", () => {
    const { rerender } = render(
      <EventTimeline
        events={[
          ev({ type: "findings_measured", payload: { node_id: "verify", cycle: 0, findings: [] } }),
        ]}
      />,
    );
    expect(screen.getByText("no findings")).toBeInTheDocument();

    rerender(
      <EventTimeline
        events={[
          ev({
            type: "findings_measured",
            payload: { node_id: "verify", cycle: 0, findings: [{ message: "a" }, { message: "b" }] },
          }),
        ]}
      />,
    );
    expect(screen.getByText("2 findings")).toBeInTheDocument();
  });
});
