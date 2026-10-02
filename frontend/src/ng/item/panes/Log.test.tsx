import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../testkit";
import { Log, POLL_MS } from "./Log";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const lines = [
  { n: -1, t: null, src: "sys", text: "", truncated: { lines: 400, bytes: 9000 } },
  { n: 401, t: "0:03", src: "sys", text: "dispatch" },
  { n: 402, t: "0:04", src: "agent", text: "reading cache.py" },
];
const answer = { "GET /worker-sessions/s1/log": [200, { session_id: "s1", status: "running", lines }] } as const;

describe("Log", () => {
  it("filters by source, and offers the whole file when the view was cut", async () => {
    stubFetch(answer as never);
    render(<Log sessionId="s1" running={false} title="t" />);
    expect(await screen.findByText(/reading cache\.py/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download the whole log" })).toHaveAttribute("href", "/api/worker-sessions/s1/log");
    await userEvent.click(screen.getByRole("button", { name: "agent" }));
    expect(screen.queryByText(/reading cache\.py/)).toBeNull();
    expect(screen.getByText(/dispatch/)).toBeInTheDocument();
  });

  it("reads again while the session runs, and not once it ended", async () => {
    vi.useFakeTimers();
    const calls = stubFetch(answer as never);
    const { rerender } = render(<Log sessionId="s1" running title="t" />);
    await act(async () => void (await vi.advanceTimersByTimeAsync(POLL_MS * 2 + 10)));
    expect(calls.length).toBe(3);
    rerender(<Log sessionId="s1" running={false} title="t" />);
    await act(async () => void (await vi.advanceTimersByTimeAsync(POLL_MS * 3)));
    expect(calls.length).toBe(4);
  });

  it("clears the no-log note once a pending attempt starts writing one", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /worker-sessions/s1/log": [404, { detail: "no log" }] };
    stubFetch(answers);
    const { rerender } = render(<Log sessionId="s1" running={false} title="t" />);
    expect(await screen.findByText("No log for this attempt yet.")).toBeInTheDocument();
    answers["GET /worker-sessions/s1/log"] = answer["GET /worker-sessions/s1/log"] as never;
    rerender(<Log sessionId="s1" running title="t" />);
    expect(await screen.findByText(/reading cache\.py/)).toBeInTheDocument();
    expect(screen.queryByText("No log for this attempt yet.")).toBeNull();
  });
});
