import { describe, expect, it } from "vitest";
import { checkText, nextCheck } from "./checks";
import { CRON_PRESETS, cronProblem, describeCron } from "./cron";
import { intakeOf } from "./types";
import { intervalFromText, showMinutes, toMinutes } from "./units";

describe("Auto-intake", () => {
  // r12: a time is UTC, as the scheduler runs (R12b-14: "61 9 * * *" read "daily 09:61").
  it.each([
    ["0 9 * * 1-5", "weekdays 09:00 UTC"],
    ["30 7 * * *", "daily 07:30 UTC"],
    ["0 9 * * 1", "Mondays 09:00 UTC"],
    ["0 9 * * 0", "Sundays 09:00 UTC"],
    ["0 9 * * 7", "Sundays 09:00 UTC"],
    ["*/15 * * * *", "every 15 minutes"],
    ["0 * * * *", "hourly"],
    ["20 * * * *", "hourly at :20"],
    ["*/1 * * * *", "every 1 minute"],
    ["0 9 1 * *", "0 9 1 * *"],
    ["nonsense", "nonsense"],
    ["61 9 * * *", "61 9 * * *"],
    ["0 24 * * *", "0 24 * * *"],
    ["0 9 * * 8", "0 9 * * 8"],
    ["*/0 * * * *", "*/0 * * * *"],
    ["*/90 * * * *", "*/90 * * * *"],
  ])("describes %j as %j, leaving what it cannot draw as written", (cron, words) => {
    expect(describeCron(cron)).toBe(words);
  });

  it.each([
    ["0 9 * * 1", null],
    ["*/5 9-17 * * 1-5", null],
    ["0 9 * *", "A schedule has five parts (minute, hour, day of month, month, day of week); this has 4. Pick a preset, or write it like 0 9 * * 1."],
    ["61 9 * * *", "The minute can be 0 to 59; 61 is outside that."],
    ["0 9 * * 8", "The day of week can be 0 to 7; 8 is outside that."],
    ["0 9 * * mon", "The day of week “mon” is not a number, a range like 1-5, a step like */15, or a list of those."],
    ["*/0 * * * *", "The minute “*/0” has a step that is not a positive number. A step looks like */15."],
    ["0 17-9 * * *", "The hour range 17-9 runs backwards."],
  ])("says in words why %j is refused", (cron, problem) => expect(cronProblem(cron)).toBe(problem));

  it("offers only presets it can draw and the scheduler runs", () => {
    for (const { cron } of CRON_PRESETS) {
      expect(cronProblem(cron)).toBeNull();
      expect(describeCron(cron)).not.toBe(cron);
    }
  });

  it("converts the interval between seconds on the wire and minutes on the page, in one place", () => {
    expect(toMinutes(300)).toBe(5);
    expect(showMinutes(90)).toBe("1.5 min");
    expect(intervalFromText("5")).toEqual({ seconds: 300 });
    expect(intervalFromText("0.5 min")).toEqual({ seconds: 30 });
    expect(intervalFromText("0.25")).toEqual({ error: "Checks cannot come more often than every 30 seconds." });
    expect(intervalFromText("")).toEqual({ error: "Enter a number of minutes above 0." });
  });

  it("says what a check found, with every skip reason in its own words", () => {
    const c = (skipped: string[]) => ({ id: 1, at: "2026-10-01T10:05:00Z", ready: 4, started: ["kraft-d71a"], skipped: skipped.map((reason, i) => ({ bead_id: `b${i}`, reason })) });
    expect(checkText(c(["above_priority_ceiling", "above_priority_ceiling", "already_item"]), 2)).toBe("4 ready · started kraft-d71a · 2 above P2 · 1 already an item");
    expect(checkText({ ...c([]), started: [], ready: 0 }, 2)).toBe("0 ready · none started");
    const words = ["above_priority_ceiling", "already_item", "epic", "max_concurrent", "daily_budget", "invalid_config"].map((r) => checkText(c([r]), 3));
    expect(words.map((w) => w.split(" · ").at(-1))).toEqual(["1 above P3", "1 already an item", "1 epic", "1 left: max at a time reached", "1 left: daily budget spent", "1 left: invalid config"]);
    expect(checkText(c(["something_new"]), 2)).toContain("1 something new");
  });

  it("narrows the resolved answer and is null while a file does not load", () => {
    const ok = { enabled: true, interval_s: 300, priority_ceiling: 2, repos: [], schedules: [] };
    expect(intakeOf({ resolved: ok } as never)).toEqual(ok);
    expect(intakeOf({ resolved: null } as never)).toBeNull();
  });
});

describe("nextCheck", () => {
  const last = "2026-10-03T10:00:00Z";
  const at = (m: number) => Date.parse(last) + m * 60_000;
  it.each([
    ["three minutes off", last, at(2), "next check in 3 min"],
    ["an hour or more off", last, at(-120), "next check in 2 h"],
    ["a day off", last, at(5 - 1440), "next check in 1 day"],
    ["days off, not thousands of minutes", last, at(5 - 3 * 1440), "next check in 3 days"],
    ["inside the last minute", last, at(4.5), "next check in under a minute"],
    ["just past due", last, at(5.5), "next check due now"],
    ["long past due: the poller restarted since", last, at(9), null],
    ["no check yet", undefined, at(0), null],
  ] as [string, string | undefined, number, string | null][])("%s", (_, from, now, text) => expect(nextCheck(from, 300, now)).toBe(text));
});
