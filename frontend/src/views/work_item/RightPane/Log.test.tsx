import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { useStore } from "../../../store";
import type { LogLine, WorkerSession } from "../../../types";
import { Log } from "./Log";

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "done", attempt: 1, round: 0, created_at: "t", started_at: null, exited_at: null,
    tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, model: null, head_sha: null,
    ...over,
  }) as WorkerSession;

const line = (n: number): LogLine => ({ n, t: null, src: "stdout", text: `line ${n}` });

/** Kraft-061w: jsdom lays nothing out, so `.log-body`'s real scrollHeight is
 *  always 0 -- that alone can't tell a fixed bounded scroller from a plain
 *  block that grows forever. Stubbing scrollHeight to grow with each line
 *  pins the actual behaviour this bug was about: follow drives scrollTop to
 *  scrollHeight on every new line, not just once at mount. */
class FakeES {
  static instances: FakeES[] = [];
  url: string;
  readyState = 1;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, (() => void)[]> = {};
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  addEventListener(type: string, fn: () => void) {
    (this.listeners[type] ??= []).push(fn);
  }
}

beforeEach(() => {
  useStore.setState({ sessionsByItem: { w1: [session()] }, connection: "open" } as never);
  FakeES.instances = [];
  vi.stubGlobal("EventSource", FakeES as unknown as typeof EventSource);
});
afterEach(() => vi.restoreAllMocks());

describe("RightPane · Log · follow", () => {
  it("pins scrollTop to scrollHeight as lines stream in while following", async () => {
    // "running" makes `live`, and therefore the default `follow` state,
    // true -- this is the case the bug report described: "logs follow but
    // the view doesn't auto-scroll".
    useStore.setState({ sessionsByItem: { w1: [session({ status: "running" })] } } as never);
    vi.spyOn(api, "getLogLines").mockResolvedValue({ session_id: "s1", status: "running", lines: [] });
    render(<Log sessionId="s1" />);

    const body = document.querySelector(".log-body") as HTMLElement;
    Object.defineProperty(body, "scrollHeight", { configurable: true, get: () => 1000 });

    const es = FakeES.instances[0];
    act(() => es.onmessage?.({ data: JSON.stringify(line(0)) }));
    await screen.findByText("line 0");
    expect(body.scrollTop).toBe(1000);

    Object.defineProperty(body, "scrollHeight", { configurable: true, get: () => 2000 });
    act(() => es.onmessage?.({ data: JSON.stringify(line(1)) }));
    await screen.findByText("line 1");
    expect(body.scrollTop).toBe(2000);
  });
});

describe("RightPane · Log", () => {
  it("shows every line with no cap", async () => {
    vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1", status: "done", lines: [line(0), line(1)],
    });
    render(<Log sessionId="s1" />);
    expect(await screen.findByText("line 0")).toBeInTheDocument();
    expect(screen.getByText("line 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show all/i })).not.toBeInTheDocument();
  });

  it("mobile m04: caps at capLines and expands on 'Show all'", async () => {
    const lines = Array.from({ length: 12 }, (_, i) => line(i));
    vi.spyOn(api, "getLogLines").mockResolvedValue({ session_id: "s1", status: "done", lines });
    render(<Log sessionId="s1" capLines={8} />);

    await screen.findByText("line 11"); // the tail is what an 8-line cap shows
    expect(screen.queryByText("line 0")).not.toBeInTheDocument();
    expect(screen.getAllByText(/^line \d+$/)).toHaveLength(8);
    const showAll = screen.getByRole("button", { name: "Show all 12 lines" });

    await userEvent.click(showAll);
    expect(screen.getByText("line 0")).toBeInTheDocument();
    expect(screen.getAllByText(/^line \d+$/)).toHaveLength(12);
    expect(screen.queryByRole("button", { name: /show all/i })).not.toBeInTheDocument();
  });

  it("Kraft-3oau: a fetch error clears once the connection reconnects", async () => {
    const spy = vi.spyOn(api, "getLogLines").mockRejectedValueOnce(new Error("offline"));
    render(<Log sessionId="s1" />);
    expect(await screen.findByText("offline")).toBeInTheDocument();

    spy.mockResolvedValue({ session_id: "s1", status: "done", lines: [line(0)] });
    act(() => useStore.setState({ connection: "reconnecting" } as never));
    act(() => useStore.setState({ connection: "open" } as never));

    expect(await screen.findByText("line 0")).toBeInTheDocument();
    expect(screen.queryByText("offline")).not.toBeInTheDocument();
  });

  it("renders one header row, with the chips in it and no title block above", async () => {
    vi.spyOn(api, "getLogLines").mockResolvedValue({
      session_id: "s1", status: "done", lines: [line(0)],
    });
    render(<Log sessionId="s1" />);
    await screen.findByText("line 0");
    expect(document.querySelector(".log-filters")).toBeNull();
    expect(document.querySelector(".log-title")).toBeNull();
    const head = document.querySelector(".log-head") as HTMLElement;
    expect(head.textContent).toContain("Log");
    expect(within(head).getByRole("button", { name: /^all$/i })).toBeTruthy();
  });
});
