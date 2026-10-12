import { describe, expect, it } from "vitest";
import type { LogLine, WorkerSession } from "../../../frontend/src/types";
import { ApiError, type WorkItemDetail } from "../../src/core/api";
import { followLog, formatLogLine, logSession, parseSse } from "../../src/core/logStream";
import { item, stopped } from "./fixtures/item";

describe("parseSse", () => {
  it.each([
    ["keeps a frame still arriving", 'data: {"n":1}\n\ndata: {"n"', [{ event: "message", data: '{"n":1}' }], 'data: {"n"'],
    ["reads a named frame", "event: end\ndata: {}\n\n", [{ event: "end", data: "{}" }], ""],
    ["ends a frame on CRLF", 'data: {"n":1}\r\n\r\nda', [{ event: "message", data: '{"n":1}' }], "da"],
  ])("%s", (_, buffer, frames, rest) => expect(parseSse(buffer)).toEqual({ frames, rest }));
});

const session = (id: string, over: Partial<WorkerSession> = {}) => ({ id, node_id: "implementation", hook_point: "implementation.work", status: "exited", ...over }) as WorkerSession;
const detail = (over: Partial<WorkItemDetail>) => item(over) as WorkItemDetail;

describe("logSession", () => {
  it.each([
    ["none in the list", detail({ worker_sessions: [] }), undefined],
    ["no list at all", detail({}), undefined],
    ["the newest while running", detail({ worker_sessions: [session("s1"), session("s2")] }), "s2"],
    ["the one the stop names", stopped("failed", {
      stop: { kind: "failed", node: "implementation", reason: null, resume_at: null, facts: { session_id: "s1" } },
      worker_sessions: [session("s1"), session("s2")],
    } as Partial<WorkItemDetail>), "s1"],
    ["the stopped node's last non-escalation session", stopped("failed", {
      stop: { kind: "failed", node: "impl", reason: null, resume_at: null },
      worker_sessions: [session("s1", { node_id: "impl" }), session("s2", { node_id: "impl", hook_point: "escalation" })],
    } as Partial<WorkItemDetail>), "s1"],
  ])("%s", (_, d, want) => expect(logSession(d as WorkItemDetail)?.id).toBe(want));
});

describe("formatLogLine", () => {
  it.each([
    ["a summarised agent line", { n: 0, t: "2026-10-12T10:11:12Z", src: "agent", text: "x", summary: "Read foo.ts" }, "10:11:12 agent  Read foo.ts"],
    ["the truncation marker", { n: -1, t: null, src: "sys", text: "… 9 earlier lines", truncated: { lines: 9, bytes: 1 } }, "--:--:-- sys    … 9 earlier lines"],
    ["ANSI and OSC escapes", { n: 1, t: null, src: "stdout", text: "\x1b[31mred\x1b[0m\x1b]0;t\x07" }, "--:--:-- stdout red"],
    ["C1 controls and 8-bit CSI", { n: 2, t: null, src: "stdout", text: "a\u009b2Jb\u0085c\u009fd" }, "--:--:-- stdout abcd"],
    ["C0 controls but tab", { n: 3, t: null, src: "stdout", text: "a\tb\rc\x07d\x7f" }, "--:--:-- stdout a\tbcd"],
  ] as [string, LogLine, string][])("%s", (_, l, want) => expect(formatLogLine(l)).toBe(want));
});

const line = (n: number): LogLine => ({ n, t: null, src: "stdout", text: `l${n}` });
const marker: LogLine = { n: -1, t: null, src: "sys", text: "… earlier", truncated: { lines: 1, bytes: 1 } };
type Step = number | "throw" | "abort" | ApiError;

/** One connect per script: its lines in order, then an `end` (returns) unless the script throws or aborts. */
async function follow(...connects: Step[][]) {
  const ctrl = new AbortController();
  const written: number[] = [];
  const lost: string[] = [];
  let i = 0;
  const open = async function* (): AsyncGenerator<LogLine> {
    const script = connects[i++] ?? ["throw"];
    for (const s of script) {
      if (s instanceof ApiError) throw s;
      if (s === "throw") throw new ApiError(0, "dropped");
      if (s === "abort") { ctrl.abort(); return; }
      yield s === -1 ? marker : line(s);
    }
  };
  const end = await followLog(open, (l) => written.push(l.n), ctrl.signal, { backoffMs: () => 0, onLost: (d) => lost.push(d) });
  return { end, written, connects: i, lost };
}

describe("followLog", () => {
  it.each([
    ["ends with the server's end", [[0, 1]], "ended", [0, 1]],
    ["reconnects after a drop without repeating lines", [[0, 1, "throw"], [0, 1, 2]], "ended", [0, 1, 2]],
    ["gives up after three failures in a row", [["throw"], ["throw"], ["throw"]], "lost", []],
    ["stops when aborted mid-stream", [[0, "abort"]], "aborted", [0]],
    ["counts a connect that closes before any line as a failure", [[0, "throw"], ["throw"], ["throw"], [1]], "lost", [0]],
    ["resets the count only on a new line, not on a replay", [[0, "throw"], [0, "throw"], [0, "throw"], [0, 1]], "lost", [0]],
    ["writes the marker once, though no real line came yet", [[-1, "throw"], [-1, 0]], "ended", [-1, 0]],
    ["writes the marker only before the first real line", [[-1, 0, "throw"], [-1, 0, 1]], "ended", [-1, 0, 1]],
  ] as [string, Step[][], string, number[]][])("%s", async (_, connects, end, written) => {
    const got = await follow(...connects);
    expect({ end: got.end, written: got.written }).toEqual({ end, written });
  });

  it.each([
    ["a 4xx after one connect", [[new ApiError(404, "unknown session")]], 1, ["unknown session"]],
    ["a drop after three", [["throw"], ["throw"], ["throw"]], 3, ["dropped"]],
  ] as [string, Step[][], number, string[]][])("says why it lost the stream: %s", async (_, connects, n, lost) => {
    const got = await follow(...connects);
    expect({ connects: got.connects, lost: got.lost }).toEqual({ connects: n, lost });
  });
});
