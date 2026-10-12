import { expect, it } from "vitest";
import { spendLine, timeLine, todayLine } from "../../src/core/spend";

it.each([
  ["under a cap", { cap_usd: 5, spent_usd: 2.41, source: "item" as const }, "Spent $2.41 of $5.00"],
  ["with no cap", { cap_usd: null, spent_usd: 0.12, source: "policy" as const }, "Spent $0.120 (no cap)"],
  ["absent", undefined, null],
  ["null", null, null],
])("spendLine: %s", (_, b, want) => {
  expect(spendLine(b)).toBe(want);
});

it.each([
  ["under a cap", { running_s: 4320, cap_minutes: 120 }, "Ran 1h 12m of 2h"],
  ["with no cap", { running_s: 240, cap_minutes: null }, "Ran 4m (no cap)"],
  ["null", null, null],
  ["absent", undefined, null],
])("timeLine: %s", (_, rt, want) => {
  expect(timeLine(rt)).toBe(want);
});

it.each([
  ["under a cap", { spent_usd: 1.2, cap_usd: 20 }, "Today: $1.20 of $20.00"],
  ["with no daily cap", { spent_usd: 0.5, cap_usd: null }, "Today: $0.500 (no daily cap)"],
  ["null", null, null],
  ["absent", undefined, null],
])("todayLine: %s", (_, t, want) => {
  expect(todayLine(t)).toBe(want);
});
