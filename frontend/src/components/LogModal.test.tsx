import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { LogLine, WorkerSession } from "../types";
import { LogModal } from "./LogModal";

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "done", attempt: 1, round: 2, created_at: "t", started_at: null, exited_at: null,
    tokens_in: 40_000, tokens_out: 1_200, cost_usd: 1.38, wall_ms: 4 * 60_000, model: "m",
    ...over,
  }) as WorkerSession;

const line = (n: number, src: LogLine["src"], text: string): LogLine => ({
  n,
  t: null,
  src,
  text,
});

const LINES = [
  line(0, "sys", "starting on.test.run"),
  line(1, "stdout", "collected 12 items"),
  line(2, "agent", '{"type":"result"}'),
];

beforeEach(() => {
  useStore.setState({ sessionsByItem: { w1: [session()] } } as never);
  vi.spyOn(api, "getLogLines").mockResolvedValue({
    session_id: "s1",
    status: "done",
    lines: LINES,
  });
});
afterEach(() => vi.restoreAllMocks());

describe("LogModal", () => {
  it("heads with the session's own state, node, cycle and usage", async () => {
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("on.test.run")).toBeInTheDocument();
    expect(screen.getByText("done · 4m")).toBeInTheDocument();
    expect(screen.getByText("verify · cycle 2")).toBeInTheDocument();
    expect(screen.getByText("41.2k tokens · $1.38")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "done" })).toBeInTheDocument();
  });

  it("filters by source and counts what is shown", async () => {
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("3 lines")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "stdout" }));
    expect(screen.getByText("1 lines")).toBeInTheDocument();
    expect(screen.getByText("collected 12 items")).toBeInTheDocument();
    expect(screen.queryByText("starting on.test.run")).toBeNull();
  });

  it("follows by default only while the session is still running", async () => {
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    expect(screen.getByRole("button", { name: /follow/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    useStore.setState({ sessionsByItem: { w1: [session({ status: "running" })] } } as never);
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    const buttons = await screen.findAllByRole("button", { name: /follow/i });
    expect(buttons[buttons.length - 1]).toHaveAttribute("aria-pressed", "true");
  });

  it("copies the whole log, not just the filtered view", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    await userEvent.click(screen.getByRole("button", { name: "stdout" }));
    await userEvent.click(screen.getByRole("button", { name: /copy log/i }));
    expect(writeText).toHaveBeenCalledWith(LINES.map((l) => l.text).join("\n"));
  });

  it("surfaces a failed fetch rather than an empty modal", async () => {
    vi.spyOn(api, "getLogLines").mockRejectedValue(new Error("unknown session"));
    render(<LogModal sessionId="nope" onClose={() => {}} />);
    expect(await screen.findByText(/unknown session/)).toHaveClass("form-error");
  });

  it("merges the snapshot with the tail instead of replacing it", async () => {
    // the tail can deliver lines past the end of the snapshot before it resolves
    type Snapshot = Awaited<ReturnType<typeof api.getLogLines>>;
    let resolveSnapshot: (v: Snapshot) => void = () => {};
    vi.spyOn(api, "getLogLines").mockReturnValue(
      new Promise<Snapshot>((r) => {
        resolveSnapshot = r;
      }),
    );
    const { rerender } = render(<LogModal sessionId="s1" onClose={() => {}} />);
    rerender(<LogModal sessionId="s1" onClose={() => {}} />);

    resolveSnapshot({ session_id: "s1", status: "done", lines: LINES.slice(0, 2) });
    expect(await screen.findByText("collected 12 items")).toBeInTheDocument();
    expect(screen.getByText("2 lines")).toBeInTheDocument();
  });
});
