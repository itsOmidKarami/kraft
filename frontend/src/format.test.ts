import { describe, expect, it } from "vitest";
import { ago, elapsed, statusWord, tokens, until, usd } from "./format";

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

describe("statusWord", () => {
  it("renders rate_limited in plain words", () => {
    expect(statusWord("rate_limited")).toBe("rate limited");
  });

  it("falls back to the raw string for anything unmapped", () => {
    expect(statusWord("active")).toBe("active");
  });
});
