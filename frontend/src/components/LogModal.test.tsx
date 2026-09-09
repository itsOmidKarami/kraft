import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { LogLine, WorkerSession } from "../types";
import { LogModal } from "./LogModal";

class FakeES {
  static instances: FakeES[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  url: string;
  readyState = 1;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, (() => void)[]> = {};
  close = vi.fn(() => {
    this.readyState = 2;
  });
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  addEventListener(type: string, fn: () => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  emit(type: string) {
    for (const fn of this.listeners[type] ?? []) fn();
  }
}

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "done", attempt: 1, round: 2, created_at: "t", started_at: null, exited_at: null,
    tokens_in: 40_000, tokens_out: 1_200, cost_usd: 1.38, wall_ms: 4 * 60_000, model: "m",
    ...over,
  }) as WorkerSession;

const line = (n: number, src: LogLine["src"], text: string, summary?: string): LogLine => ({
  n,
  t: null,
  src,
  text,
  summary,
});

const LINES = [
  line(0, "sys", "starting on.test.run"),
  line(1, "stdout", "collected 12 items"),
  line(2, "agent", '{"type":"result"}', "result: success"),
];

beforeEach(() => {
  FakeES.instances = [];
  vi.stubGlobal("EventSource", FakeES as unknown as typeof EventSource);
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

  it("shows the readable summary when the server sent one", async () => {
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("result: success")).toBeInTheDocument();
    expect(screen.queryByText('{"type":"result"}')).not.toBeInTheDocument();
  });

  it("copies the whole log from the plain-text endpoint, not the rendered view", async () => {
    // the rendered rows are truncated at 2000 chars and summarised; a copied
    // log has to be the file
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    vi.spyOn(api, "getLogText").mockResolvedValue("the whole log\n");
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    await userEvent.click(screen.getByRole("button", { name: "stdout" }));
    await userEvent.click(screen.getByRole("button", { name: /copy log/i }));
    expect(api.getLogText).toHaveBeenCalledWith("s1");
    expect(writeText).toHaveBeenCalledWith("the whole log\n");
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

  // ── a modal that does not lie about its stream (Kraft-29a0) ─────────────

  const running = () =>
    useStore.setState({ sessionsByItem: { w1: [session({ status: "running" })] } } as never);

  it("does not fetch the snapshot while following the stream", async () => {
    // the server's tail replays from line 0, so the snapshot can only duplicate
    // the stream or fail -- it has no third outcome
    running();
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    expect(api.getLogLines).not.toHaveBeenCalled();
    expect(FakeES.instances).toHaveLength(1);
  });

  it("fetches the snapshot when following is switched off", async () => {
    running();
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    await userEvent.click(screen.getByRole("button", { name: /following/i }));
    expect(api.getLogLines).toHaveBeenCalledTimes(1);
    expect(FakeES.instances[0].close).toHaveBeenCalled();
  });

  it("clears the previous session's error when the session changes", async () => {
    vi.spyOn(api, "getLogLines").mockRejectedValueOnce(new Error("unknown session"));
    const { container, rerender } = render(<LogModal sessionId="nope" onClose={() => {}} />);
    await screen.findByText(/unknown session/);

    vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1",
      status: "done",
      lines: LINES,
    });
    rerender(<LogModal sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("collected 12 items")).toBeInTheDocument();
    expect(container.querySelector(".form-error")).toBeNull();
  });

  it("stops claiming to follow when the stream ends", async () => {
    running();
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    await act(async () => FakeES.instances[0].emit("end"));
    expect(screen.getByRole("button", { name: /follow/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByText(/following · new lines appear here/)).toBeNull();
  });

  it("keeps the stream open when the browser is going to retry", async () => {
    running();
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    const es = FakeES.instances[0];

    es.readyState = FakeES.CONNECTING;
    await act(async () => es.onerror!());
    expect(es.close).not.toHaveBeenCalled();
    expect(screen.getByText(/reconnecting/)).toBeInTheDocument();

    es.readyState = FakeES.CLOSED;
    await act(async () => es.onerror!());
    expect(screen.getByRole("button", { name: /follow/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByText(/log stream stopped/)).toBeInTheDocument();
  });

  it("says no output yet for a live session with an empty log", async () => {
    // an agent run with --output-format json writes nothing until it exits
    // (adapters/subprocess.py:148) -- the empty screen the bug was reported on
    running();
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    expect(screen.getByText(/no output yet/)).toBeInTheDocument();
  });

  it("does not claim an empty log when a chip filtered every line away", async () => {
    // a session writes one family of src, so every other chip empties the view
    vi.mocked(api.getLogLines).mockResolvedValue({
      session_id: "s1",
      status: "done",
      lines: [line(0, "stdout", "already merged (!77); nothing to do")],
    });
    render(<LogModal sessionId="s1" onClose={() => {}} />);
    await screen.findByText("on.test.run");
    await userEvent.click(screen.getByRole("button", { name: "agent" }));
    expect(screen.queryByText(/has not written a line/)).not.toBeInTheDocument();
    expect(screen.getByText(/no agent lines — this session logged 1/)).toBeInTheDocument();
  });
});
