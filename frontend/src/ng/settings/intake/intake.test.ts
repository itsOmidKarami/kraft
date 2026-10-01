import { describe, expect, it } from "vitest";
import { checkText } from "./checks";
import { describeCron } from "./cron";
import { intakeOf } from "./types";
import { intervalFromText, showMinutes, toMinutes } from "./units";

describe("Auto-intake", () => {
  it("describes the cron shapes the page draws and leaves the rest as written", () => {
    expect(describeCron("0 9 * * 1-5")).toBe("weekdays 09:00");
    expect(describeCron("30 7 * * *")).toBe("daily 07:30");
    expect(describeCron("0 9 * * 1")).toBe("Mondays 09:00");
    expect(describeCron("0 9 * * 0")).toBe("Sundays 09:00");
    expect(describeCron("*/15 * * * *")).toBe("every 15 minutes");
    expect(describeCron("0 9 1 * *")).toBe("0 9 1 * *");
    expect(describeCron("nonsense")).toBe("nonsense");
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
    const ok = { enabled: true, interval_s: 300, max_concurrent: 3, priority_ceiling: 2, repos: [], schedules: [] };
    expect(intakeOf({ resolved: ok } as never)).toEqual(ok);
    expect(intakeOf({ resolved: null } as never)).toBeNull();
  });
});
