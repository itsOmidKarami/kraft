import { act, fireEvent, render, screen, within } from "@testing-library/react";
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
    expect(screen.getByText("1 line")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "full screen" }));
    expect(screen.getByRole("dialog").querySelector(".ip-log-screen-head")).toHaveTextContent("log · 1 line");
  });

  it("goes full screen over the viewport with the crumb, the task, the count and the controls, and Escape leaves it", async () => {
    stubFetch(answer as never);
    render(<Log sessionId="s1" running={false} title="implement" crumb="default › implementation" />);
    await screen.findByText(/reading cache\.py/);
    const open = screen.getByRole("button", { name: "full screen" });
    await userEvent.click(open);
    const screen_ = screen.getByRole("dialog", { name: "implement · log" });
    expect(screen_).toHaveClass("ip-log-screen");
    expect(screen_.querySelector(".ip-log-screen-head")).toHaveTextContent(/^default › implementation › implementlog · 2 linessysstdoutagenttool followcopy⤡ exit full screen$/);
    expect(within(screen_).getByRole("button", { name: "agent" })).toHaveAttribute("aria-pressed", "true");
    expect(within(screen_).getByText(/reading cache\.py/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(open).toHaveFocus();
    await userEvent.click(open);
    await userEvent.click(screen.getByRole("button", { name: "⤡ exit full screen" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens at its newest line, scrolling the <pre> it fills the pane with", async () => {
    stubFetch(answer as never);
    const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    render(<Log sessionId="s1" running={false} title="t" />);
    await screen.findByText(/reading cache\.py/);
    expect(screen.getByLabelText("Log lines").scrollTop).toBe(900);
    height.mockRestore();
  });

  it("stops following once the reader scrolls up, and resumes at the bottom", async () => {
    stubFetch(answer as never);
    const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    const client = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
    render(<Log sessionId="s1" running={false} title="t" />);
    await screen.findByText(/reading cache\.py/);
    const pre = screen.getByLabelText("Log lines");
    const follow = screen.getByRole("checkbox", { name: "follow" });
    expect(follow).toBeChecked();
    pre.scrollTop = 100;
    fireEvent.scroll(pre);
    expect(follow).not.toBeChecked();
    pre.scrollTop = 600;
    fireEvent.scroll(pre);
    expect(follow).toBeChecked();
    height.mockRestore();
    client.mockRestore();
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
