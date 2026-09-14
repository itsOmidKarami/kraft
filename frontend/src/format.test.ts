import { describe, expect, it } from "vitest";
import { ago, docBody, elapsed, elapsedBetween, logLineText, nodeRunSpan, shortId, statusWord, tokens, until, usd } from "./format";
import type { KraftEvent, LogLine, WorkerSession } from "./types/work_item";

const logLine = (over: Partial<LogLine>): LogLine => ({
  n: 0,
  t: null,
  src: "agent",
  text: "",
  ...over,
});

const now = Date.parse("2026-09-04T12:00:00Z");
const at = (ms: number) => new Date(now - ms).toISOString();

describe("ago", () => {
  it("steps through the units and stays quiet on bad input", () => {
    expect(ago(at(30_000), now)).toBe("just now");
    expect(ago(at(12 * 60_000), now)).toBe("12m ago");
    expect(ago(at(3 * 3_600_000), now)).toBe("3h ago");
    expect(ago(at(4 * 86_400_000), now)).toBe("4d ago");
    expect(ago(null, now)).toBe("");
    expect(ago("not a date", now)).toBe("");
  });
});

describe("elapsed", () => {
  it("drops the minutes only when they are zero", () => {
    expect(elapsed(45_000)).toBe("45s");
    expect(elapsed(4 * 60_000)).toBe("4m");
    expect(elapsed(2 * 3_600_000)).toBe("2h");
    expect(elapsed(2 * 3_600_000 + 5 * 60_000)).toBe("2h 5m");
    expect(elapsed(51 * 3_600_000)).toBe("2d 3h");
  });

  it("reads a negative span as 0s", () => {
    expect(elapsed(-31_317_000)).toBe("0s");
  });
});

describe("shortId", () => {
  it("keeps the first 8 and last 5 of a 32-hex id and passes short ids through", () => {
    expect(shortId("8cbfe6e27c1044b3e445c0f0d726357d")).toBe("8cbfe6e2…6357d");
    expect(shortId("ses_b71e0")).toBe("ses_b71e0");
  });
});

describe("elapsedBetween + nodeRunSpan (W0.4)", () => {
  const ev = (seq: number, type: string, node_id: string, ms: number) =>
    ({ seq, work_item_id: "w", type, payload: { node_id }, created_at: at(ms) }) as KraftEvent;
  const ses = (over: Partial<WorkerSession>) =>
    ({
      id: "s", work_item_id: "w", node_id: "verify", hook_point: "on.test.run", status: "running",
      attempt: 1, round: 0, created_at: at(3_500_000), started_at: at(3_500_000), exited_at: null,
      ...over,
    }) as WorkerSession;
  const span = (sessions: WorkerSession[]) => {
    const s = nodeRunSpan("verify", [ev(1, "node_started", "verify", 3_600_000)], sessions)!;
    return elapsedBetween(s.from, s.to, now);
  };

  it("freezes a node once its latest session has exited", () => {
    expect(span([ses({ status: "capped_out", exited_at: at(3_000_000) })])).toBe("10m");
  });

  it("keeps counting while the latest session runs", () => {
    expect(span([ses({ status: "running" })])).toBe("1h");
  });

  it("does not restart the clock for an escalation turn", () => {
    expect(span([ses({ status: "done", exited_at: at(3_000_000) }), ses({ id: "e", hook_point: "escalation", created_at: at(60_000) })])).toBe("10m");
  });

  it("reads 0s for a session start in the future", () => {
    expect(elapsedBetween(new Date(now + 31_317_000).toISOString(), null, now)).toBe("0s");
  });

  it("ends a completed node at node_completed", () => {
    const s = nodeRunSpan("verify", [ev(1, "node_started", "verify", 3_600_000), ev(2, "node_completed", "verify", 1_800_000)], [])!;
    expect(elapsedBetween(s.from, s.to, now)).toBe("30m");
  });
});

describe("tokens / usd", () => {
  it("scales token counts and keeps small costs readable", () => {
    expect(tokens(980)).toBe("980");
    expect(tokens(41_200)).toBe("41.2k");
    expect(tokens(138_000)).toBe("138k");
    expect(tokens(1_400_000)).toBe("1.4M");
    expect(usd(2.415)).toBe("$2.42");
    expect(usd(0.0125)).toBe("$0.013");
    // an incomplete sum is a floor, and says so
    expect(usd(2.415, false)).toBe("$2.42+");
  });
});

describe("until", () => {
  it("points forwards, so a deadline never reads as 'just now'", () => {
    const now = Date.parse("2026-09-04T12:00:00Z");
    const inMs = (ms: number) => new Date(now + ms).toISOString();
    expect(until(inMs(7 * 86_400_000), now)).toBe("in 7d");
    expect(until(inMs(3 * 3_600_000), now)).toBe("in 3h");
    expect(until(inMs(30_000), now)).toBe("in 1m");
    expect(until(inMs(-1000), now)).toBe("expired");
    expect(until(null, now)).toBe("");
  });
});

describe("logLineText", () => {
  it("uses a real server summary", () => {
    const l = logLine({ text: '{"type":"result"}', summary: "result: success" });
    expect(logLineText(l)).toBe("result: success");
  });

  it("falls back to the parsed type when the server's summary is just the raw line", () => {
    // logs.py summary() returns _shorten(line) for a shape it doesn't
    // recognise (e.g. tool_progress), so `summary` equals `text` -- that is
    // not a real summary and must not render as raw JSON (Kraft-av3t).
    const raw = '{"type":"tool_progress","tokens":12}';
    const l = logLine({ text: raw, summary: raw });
    expect(logLineText(l)).toBe("tool_progress");
  });

  it("falls back to the parsed type with no summary at all", () => {
    const l = logLine({ text: '{"type":"tool_progress"}' });
    expect(logLineText(l)).toBe("tool_progress");
  });

  it("passes plain stdout/sys text through untouched", () => {
    const l = logLine({ src: "stdout", text: "collected 12 items" });
    expect(logLineText(l)).toBe("collected 12 items");
  });
});

describe("statusWord", () => {
  it("renders rate_limited in plain words", () => {
    expect(statusWord("rate_limited")).toBe("rate limited");
  });

  it("renders waiting in plain words", () => {
    expect(statusWord("waiting")).toBe("waiting on CI");
  });

  it("falls back to the raw string for anything unmapped", () => {
    expect(statusWord("active")).toBe("active");
  });
});

describe("docBody (W8.2)", () => {
  it("drops a leading H1 that repeats the title and headings with no body", () => {
    const md = "# Reuse what we measured\n\nIntro.\n\n## Summary\n\n## Plan\n\nStep one.\n\n## Notes\n";
    expect(docBody(md, "Reuse what we measured")).toBe("\nIntro.\n\n\n## Plan\n\nStep one.\n\n");
  });

  it("keeps a heading whose body is a deeper heading, and leaves fenced code alone", () => {
    const md = "## Design\n\n### Cache\n\ntext\n\n```sh\n# not a heading\n```";
    expect(docBody(md, "Other")).toBe(md);
  });
});
