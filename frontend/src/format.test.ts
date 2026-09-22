// @vitest-environment node
import { describe, expect, it } from "vitest";
import { ago, cleanTitle, docBody, docTitle, elapsed, runLabel, elapsedBetween, logLineText, nodeRunSpan, shortId, statusWord, tokenSplit, tokenTotal, tokens, until, usd } from "./format";
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

  it("keeps counting when a fast builtin was created after the live agent", () => {
    // Kraft-s7c04.47: `implementation` creates on.repos.scan 0.5 ms after
    // on.implementation.start and it exits in 61 ms. Taking the last-CREATED
    // session froze a 75-minute node at "0s".
    expect(
      span([
        ses({ id: "agent", hook_point: "on.implementation.start", status: "running", created_at: at(3_500_000) }),
        ses({ id: "scan", hook_point: "on.repos.scan", status: "done", created_at: at(3_499_999), exited_at: at(3_499_000) }),
      ]),
    ).toBe("1h");
  });

  it("ends a finished node at the latest exit, not the last-created session's", () => {
    // The same bug backwards: a completed node whose fast hook was created
    // last used to end at the builtin's exit rather than the agent's.
    expect(
      span([
        ses({ id: "agent", hook_point: "on.implementation.start", status: "done", created_at: at(3_500_000), exited_at: at(600_000) }),
        ses({ id: "scan", hook_point: "on.repos.scan", status: "done", created_at: at(3_499_999), exited_at: at(3_499_000) }),
      ]),
    ).toBe("50m");
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

describe("tokenTotal / tokenSplit (Ruling 211)", () => {
  const split = { tokens_in: 1_200, tokens_cache_write: 3_000, tokens_cache_read: 80_000, tokens_out: 11_000 };

  it("counts every kind, the cache included, in the one number shown", () => {
    expect(tokenTotal(split)).toBe(95_200);
    expect(tokenTotal({ tokens_in: null, tokens_out: null })).toBe(0);
  });

  it("names each kind apart", () => {
    expect(tokenSplit(split)).toBe("1.2k in · 3k cache write · 80k cache read · 11k out");
  });

  it.each([
    ["an older session, its cache kinds null", { tokens_in: 5_000, tokens_cache_write: null, tokens_cache_read: null, tokens_out: 10 }],
    ["an older session, from before the fields", { tokens_in: 5_000, tokens_out: 10 }],
    ["a rollup holding older sessions", { ...split, tokens_in: 5_000, tokens_out: 10, split_complete: false }],
  ])("says so when the split is unknown: %s", (_name, r) => {
    expect(tokenSplit(r)).toMatch(/^5k in \(cache not split on older sessions\) · /);
    expect(tokenSplit(r)).toMatch(/ · 10 out$/);
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
  // logs.py summary() returns _shorten(line) for a shape it doesn't recognise
  // (e.g. tool_progress), so `summary` equal to `text` is not a real summary
  // and must not render as raw JSON (Kraft-av3t).
  const raw = '{"type":"tool_progress","tokens":12}';
  it.each<[string, Partial<LogLine>, string]>([
    ["uses a real server summary", { text: '{"type":"result"}', summary: "result: success" }, "result: success"],
    ["falls back to the parsed type when the summary is just the raw line", { text: raw, summary: raw }, "tool_progress"],
    ["falls back to the parsed type with no summary at all", { text: '{"type":"tool_progress"}' }, "tool_progress"],
    ["passes plain stdout/sys text through untouched", { src: "stdout", text: "collected 12 items" }, "collected 12 items"],
  ])("%s", (_, over, want) => {
    expect(logLineText(logLine(over))).toBe(want);
  });
});

describe("statusWord", () => {
  it.each([
    ["rate_limited", "rate limited"],
    ["waiting", "waiting on CI"],
    ["active", "active"], // anything unmapped falls back to the raw string
  ])("renders %s as %s", (status, word) => {
    expect(statusWord(status)).toBe(word);
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

describe("cleanTitle (W13 · B.3)", () => {
  const item = { title: "Chain review: cover hook configs and escalation logic", bead_id: "Kraft-df4tc" };
  const bare = { title: "T", bead_id: null };
  it.each<[string, string, { title: string; bead_id: string | null } | undefined, string]>([
    ["drops a trailing bead id in parentheses", "Chain-review diff review (Kraft-df4tc)", item, "Chain-review diff review"],
    ["drops an em dash, the bead id and everything after it", "Security review — Kraft-df4tc (chain review: hook configs and escalation logic)", item, "Security review"],
    ["leaves a title with no bead id or item title alone", "Fix-loop judge: verify (round 4 decision)", item, "Fix-loop judge: verify (round 4 decision)"],
    ["is empty for a title that is only the item's title", "Chain review: cover hook configs and escalation logic", item, ""],
    ["takes the item's title out only as whole words", "Review: Design the caching layer", bare, "Review: Design the caching layer"],
    ["takes the item's title out as a trailing phrase", "Security review of chain review: cover hook configs and escalation logic", item, "Security review of"],
    ["drops a leading `Bead-id:`", "Kraft-df4tc: tighten the judge prompt", item, "tighten the judge prompt"],
    ["still finds a bead id without the item", "Chain-review diff review (Kraft-df4tc)", undefined, "Chain-review diff review"],
  ])("%s", (_, title, it_, want) => {
    expect(cleanTitle({ title }, it_)).toBe(want);
  });
});

describe("runLabel (W13 · B.1)", () => {
  it("names a turn, a round or an attempt from the hook and the run", () => {
    expect(runLabel("escalation", 2, 0)).toBe("turn 2");
    expect(runLabel("on.test.run", 1, 3)).toBe("round 3");
    expect(runLabel("on.fix.apply", 1, 0)).toBe("round 0");
    expect(runLabel("on.judge.decide", 1, 0)).toBe("round 0");
    expect(runLabel("on.review.requested", 2, 0)).toBe("attempt 2");
  });
  it("is null when the server sent no run info", () => {
    expect(runLabel("on.test.run", undefined, undefined)).toBeNull();
    expect(runLabel("on.test.run", null, null)).toBeNull();
  });
});

describe("docTitle (W11 · H)", () => {
  it("passes a real title through unchanged", () => {
    expect(docTitle({ title: "WS transport design", content: "Something else entirely." })).toBe("WS transport design");
  });

  it("titles a heading-less summary with its first sentence", () => {
    expect(
      docTitle({
        title: "8cbfe6e27c1044b3e445c0f0d726357d",
        content: "\nReviewed the invalidation path against main. The check holds.\n\n## Findings\n",
      }),
    ).toBe("Reviewed the invalidation path against main.");
  });

  it("reads past front matter, headings, quotes and table rows to the first prose line, without its markdown", () => {
    const content = "---\nauthor: claude\n---\n\n# Summary\n\n> quoted\n\n| a | b |\n\n**Opened** the merge request from the plan";
    expect(docTitle({ title: "", content })).toBe("Opened the merge request from the plan");
  });

  it("treats `Session <id>`, a short id and the session's own id as no title", () => {
    const content = "Rebased onto main and re-ran the verify node's tests.";
    for (const title of ["Session 8cbfe6e27c1044b3e445c0f0d726357d", "8cbfe6e2…6357d", "s-123"]) {
      expect(docTitle({ title, content, worker_session_id: "s-123" })).toBe(content);
    }
  });

  it("cuts an opening line with no sentence end at 90 characters", () => {
    const t = docTitle({ title: "", content: "word ".repeat(40) });
    expect(t.length).toBeLessThanOrEqual(90);
    expect(t.endsWith("…")).toBe(true);
  });

  it("prefixes Session · only when the derived line is under 12 characters", () => {
    expect(docTitle({ title: "", content: "Fixed it." })).toBe("Session · Fixed it.");
    expect(docTitle({ title: "", content: "Fixed the flaky import test." })).toBe("Fixed the flaky import test.");
  });

  it("never returns a bare id: with no body to read it names the hook", () => {
    expect(docTitle({ title: "8cbfe6e27c1044b3e445c0f0d726357d", hook_point: "on.mr.describe" })).toBe("Session · on.mr.describe");
    expect(docTitle({ title: "Session 8cbfe6e27c1044b3e445c0f0d726357d", kind: "sessions" })).toBe("Session · sessions");
  });
});
