import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { KraftEvent, WorkerSession } from "../../../types";
import { Events } from "./Events";

const ev = (seq: number, type: string, at: string, payload: Record<string, unknown> = {}): KraftEvent => ({
  seq,
  work_item_id: "w1",
  type,
  created_at: at,
  payload: { node_id: "verify", ...payload },
});

const findingsEvent = () =>
  ev(1, "findings_measured", "2024-01-01T00:00:00Z", {
    findings: [{ severity: "minor", message: "unused import", file: "a.py", line: 3, source_plugin: "ruff" }],
  });

describe("Events (W13 · D)", () => {
  // Kraft-a4js: the gate card only ever gives a count and links here -- this
  // is where the message, file and severity themselves must be readable.
  it("lists a findings_measured event's findings under a count", () => {
    render(<Events events={[findingsEvent()]} nodeId="verify" onViewLog={() => {}} />);
    const row = document.querySelector('.stream-row[data-kind="findings"]') as HTMLElement;
    expect(within(row).getByText("1 finding")).toBeInTheDocument();
    expect(within(row).getByText("unused import")).toHaveAttribute("title", "unused import");
    expect(within(row).getByTitle("a.py:3")).toHaveAttribute("data-allow-ellipsis");
    expect(within(row).getByText("minor")).toBeInTheDocument();
  });

  it("streams the node when nothing is selected, and a session as its own header with view log", () => {
    const sessions = [
      { id: "s1", node_id: "verify", hook_point: "on.test.run", status: "done", attempt: 1, round: 0, created_at: "2024-01-01T00:00:00Z", started_at: "2024-01-01T00:00:00Z", exited_at: "2024-01-01T00:01:02Z" } as WorkerSession,
    ];
    const events = [
      ev(1, "node_started", "2024-01-01T00:00:00Z"),
      ev(2, "worker_session_created", "2024-01-01T00:00:00Z", { session_id: "s1", hook_point: "on.test.run" }),
      ev(3, "worker_session_exited", "2024-01-01T00:01:02Z", { session_id: "s1", status: "done" }),
      ev(4, "judge_verdict", "2024-01-01T00:01:10Z", { verdict: "stop", reasoning: "nothing left to fix" }),
    ];
    const { unmount } = render(<Events events={events} sessions={sessions} nodeId="verify" onViewLog={() => {}} />);
    const pane = screen.getByTestId("right-pane-events");
    expect(pane.querySelector(".stream-head")).toHaveTextContent(/^verify · 4 events/);
    expect([...pane.querySelectorAll(".stream-row")].map((r) => r.getAttribute("data-kind"))).toEqual(["event", "session", "judge"]);
    expect(within(pane).getByText("judge · stop")).toBeInTheDocument();
    expect(within(pane).getByText("nothing left to fix")).toBeInTheDocument();
    unmount();

    render(<Events events={events} sessions={sessions} nodeId="verify" selection={{ kind: "session", id: "s1" }} onViewLog={() => {}} />);
    const head = screen.getByTestId("right-pane-events").querySelector("header")!;
    expect(head.querySelector(".stream-head")).toHaveTextContent("on.test.run · done · 1m");
    expect(within(head).getByRole("button", { name: "view log" })).toBeInTheDocument();
    expect(within(head).getByTitle("s1")).toBeInTheDocument();
  });

  it("names a round in its header: duration, sessions, findings and the verdict", () => {
    const events = [
      ev(1, "node_started", "2024-01-01T00:00:00Z"),
      ev(2, "fix_cycle_started", "2024-01-01T00:02:00Z", { cycle: 1 }),
      ev(3, "findings_measured", "2024-01-01T00:05:00Z", { findings: [{ severity: "critical", message: "m", file: "f.py", line: 1 }] }),
      ev(4, "judge_verdict", "2024-01-01T00:15:00Z", { verdict: "stop", reasoning: "done" }),
    ];
    render(<Events events={events} nodeId="verify" selection={{ kind: "round", node: "verify", n: 1 }} onViewLog={() => {}} />);
    expect(screen.getByTestId("right-pane-events").querySelector(".stream-head")).toHaveTextContent("round 2 · 13m · 0 sessions · 1 finding → stop");
  });
});
