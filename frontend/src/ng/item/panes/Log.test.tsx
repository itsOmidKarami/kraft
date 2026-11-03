import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../testkit";
import { AGENT_LINES_EARLIER, Log, POLL_MS } from "./Log";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const lines = [
  { n: -1, t: null, src: "sys", text: "", truncated: { lines: 400, bytes: 9000 } },
  { n: 401, t: "0:03", src: "sys", text: "dispatch" },
  { n: 402, t: "0:04", src: "agent", text: "reading cache.py" },
];
const answer = { "GET /work-items/w1/sessions/s1/log": [200, { session_id: "s1", status: "running", lines }] } as const;

describe("Log", () => {
  it("filters by source, and offers the whole file when the view was cut", async () => {
    stubFetch(answer as never);
    render(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
    expect(await screen.findByText(/reading cache\.py/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download the whole log" })).toHaveAttribute("href", "/api/work-items/w1/sessions/s1/log");
    // A session id is unique only within its item: the file is named for both.
    expect(screen.getByRole("link", { name: "Download the whole log" })).toHaveAttribute("download", "w1-s1.log");
    // LV-10: copy wraps with follow, never alone; full screen is the one that drops a row.
    const group = screen.getByRole("button", { name: "copy" }).parentElement!;
    expect(group).toContainElement(screen.getByRole("checkbox", { name: "follow" }));
    expect(group).not.toContainElement(screen.getByRole("button", { name: "full screen" }));
    await userEvent.click(screen.getByRole("button", { name: "agent" }));
    expect(screen.queryByText(/reading cache\.py/)).toBeNull();
    expect(screen.getByText(/dispatch/)).toBeInTheDocument();
    expect(screen.getByText("1 line")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "full screen" }));
    expect(screen.getByRole("dialog").querySelector(".ip-log-screen-head")).toHaveTextContent("log · 1 line");
  });

  it("goes full screen over the viewport with the crumb, the task, the count and the controls, and Escape leaves it", async () => {
    stubFetch(answer as never);
    render(<Log workItemId="w1" sessionId="s1" running={false} title="implement" crumb="default › implementation" />);
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
    render(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
    await screen.findByText(/reading cache\.py/);
    expect(screen.getByLabelText("Log lines").scrollTop).toBe(900);
    height.mockRestore();
  });

  it("stops following once the reader scrolls up, and resumes at the bottom", async () => {
    stubFetch(answer as never);
    const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    const client = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
    render(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
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
    const { rerender } = render(<Log workItemId="w1" sessionId="s1" running title="t" />);
    await act(async () => void (await vi.advanceTimersByTimeAsync(POLL_MS * 2 + 10)));
    expect(calls.length).toBe(3);
    rerender(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
    await act(async () => void (await vi.advanceTimersByTimeAsync(POLL_MS * 3)));
    expect(calls.length).toBe(4);
  });

  it("clears the no-log note once a pending attempt starts writing one", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1/sessions/s1/log": [404, { detail: "no log" }] };
    stubFetch(answers);
    const { rerender } = render(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
    expect(await screen.findByText("No log for this attempt yet.")).toBeInTheDocument();
    answers["GET /work-items/w1/sessions/s1/log"] = answer["GET /work-items/w1/sessions/s1/log"] as never;
    rerender(<Log workItemId="w1" sessionId="s1" running title="t" />);
    expect(await screen.findByText(/reading cache\.py/)).toBeInTheDocument();
    expect(screen.queryByText("No log for this attempt yet.")).toBeNull();
  });

  describe("sub-agents", () => {
    const sub = { agent_label: "Explore codebase", agent_type: "Explore" };
    const agentLines = [
      { n: 1, t: null, src: "agent", text: "main says hi", summary: "main says hi" },
      { n: 2, t: null, src: "agent", text: "x", summary: "↳ Explore codebase: Grep(foo)", agent: "a1", ...sub },
      { n: 3, t: null, src: "agent", text: "x", summary: "↳ Explore codebase: done", agent: "a1", agent_status: "completed", ...sub },
      { n: 4, t: null, src: "agent", text: "x", summary: "↳ Review diff: Read(a.py)", agent: "a2", agent_label: "Review diff" },
      { n: 5, t: null, src: "stdout", text: "plain stdout line" },
    ];
    const serve = (rows: unknown[]) => stubFetch({ "GET /work-items/w1/sessions/s1/log": [200, { session_id: "s1", status: "running", lines: rows }] } as never);
    const Controlled = ({ onAgent, start }: { onAgent?: (a: string | undefined) => void; start?: string }) => {
      const [agent, setAgent] = useState(start);
      return <Log workItemId="w1" sessionId="s1" running={false} title="t" agent={agent} onAgent={(a) => { setAgent(a); onAgent?.(a); }} />;
    };

    it("shows no agent menu when no row has an agent", async () => {
      stubFetch(answer as never);
      render(<Log workItemId="w1" sessionId="s1" running={false} title="t" />);
      await screen.findByText(/reading cache\.py/);
      expect(screen.queryByRole("button", { name: /^agent:/ })).toBeNull();
    });

    it("filters to one sub-agent from the agent menu", async () => {
      serve(agentLines);
      const onAgent = vi.fn();
      render(<Controlled onAgent={onAgent} />);
      await screen.findByText(/main says hi/);
      await userEvent.click(screen.getByRole("button", { name: "agent: All" }));
      await userEvent.click(screen.getByRole("menuitemradio", { name: /Explore codebase/ }));
      expect(onAgent).toHaveBeenCalledWith("a1");
      expect(screen.queryByText(/main says hi/)).toBeNull();
      expect(screen.queryByText(/Review diff/)).toBeNull();
      expect(screen.getAllByText(/↳ Explore codebase/)).toHaveLength(2);
      expect(screen.getByText(/plain stdout line/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "agent: Explore codebase" })).toBeInTheDocument();
    });

    it("says when a sub-agent's lines are before the part shown", async () => {
      serve(agentLines);
      render(<Controlled start="gone" />);
      expect(await screen.findByText(new RegExp(AGENT_LINES_EARLIER))).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Download the whole log" })).toBeInTheDocument();
    });

    it("copies what is shown, prefix and all", async () => {
      serve(agentLines);
      const writeText = vi.fn();
      vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
      render(<Controlled start="a1" />);
      await screen.findByText(/plain stdout line/);
      await userEvent.click(screen.getByRole("button", { name: "copy" }));
      expect(writeText.mock.calls[0][0]).toContain("↳ Explore codebase: Grep(foo)");
      expect(writeText.mock.calls[0][0]).not.toContain("main says hi");
    });
  });
});
