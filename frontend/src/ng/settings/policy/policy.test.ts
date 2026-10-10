import { describe, expect, it } from "vitest";
import { capKey, KEYS, SEVERITY_KEY, STUCK_KEY } from "./keys";
import { sectionOfKey, sectionOfProblem, SECTIONS } from "./sections";
import { parse, parseAge, parseSize, raw, show } from "./units";

describe("Policy sections", () => {
  it("puts every key the page edits in exactly one section", () => {
    const keys = [...Object.values(KEYS).map((k) => k.key), SEVERITY_KEY.key, STUCK_KEY.key, capKey("default", "tasks", "time_cap_minutes").key, capKey("maximum", "work_item", "budget_usd").key, "loops.verification.fix_loop.attempts"];
    for (const k of keys) expect(sectionOfKey(k), k).not.toBeNull();
    expect(sectionOfKey(KEYS.daily.key)).toBe("limits");
    expect(sectionOfKey(KEYS.attemptsMax.key)).toBe("loops");
    expect(sectionOfKey(KEYS.loopWall.key)).toBe("loops");
    expect(sectionOfKey(KEYS.concurrent.key)).toBe("housekeeping");
    expect(sectionOfKey(KEYS.relaunch.key)).toBe("housekeeping");
    expect(sectionOfKey(KEYS.forge.key)).toBe("housekeeping");
    expect(sectionOfKey("allowed_tools")).toBeNull();
  });

  it("gives every section at least one key", () => {
    const keys = [...Object.values(KEYS).map((k) => k.key), SEVERITY_KEY.key, STUCK_KEY.key];
    for (const s of SECTIONS) expect(keys.some((k) => sectionOfKey(k) === s.id), s.id).toBe(true);
  });

  it("places a problem by the key it names, else by its group", () => {
    expect(sectionOfProblem({ scope: "limits", field: "maxima.max_attempts" })).toBe("loops");
    expect(sectionOfProblem({ scope: "limits", field: "defaults.tasks.time_cap_minutes" })).toBe("limits");
    expect(sectionOfProblem({ scope: "limits", field: null, path: "default" })).toBe("limits");
    expect(sectionOfProblem({ scope: "housekeeping", field: "archive.after_days" })).toBe("housekeeping");
    expect(sectionOfProblem({ scope: "retries", field: null })).toBe("housekeeping");
    expect(sectionOfProblem({ scope: "harnesses", field: "allowed_harnesses" })).toBeNull();
  });
});

describe("Policy numbers", () => {
  it("writes each unit the way the prototype does, and unset as not set or no bound", () => {
    expect(show("min", 480)).toBe("480 min");
    expect(show("usd", 20)).toBe("$20");
    expect(show("tok", 2000000)).toBe("2,000,000");
    expect(show("days", 1)).toBe("1 day");
    expect(show("s-as-min", 3600)).toBe("60 min");
    expect(show("min", null)).toBe("not set");
    expect(show("min", null, true)).toBe("no bound");
  });

  it("parses what is typed, tolerating units and $, and clears on blank", () => {
    expect(parse("min", "45 min")).toEqual({ value: 45 });
    expect(parse("usd", "$12.5")).toEqual({ value: 12.5 });
    expect(parse("tok", "2,000,000")).toEqual({ value: 2000000 });
    // r12 review: dollars read as the budget editor reads them; stripping commas saved "0,5" as $5.
    expect(parse("usd", "0,5")).toEqual({ value: 0.5 });
    expect(parse("usd", "1,000")).toEqual({ error: "Type the amount plainly, like 1000 or 1.5." });
    expect(parse("usd", "Infinity")).toEqual({ error: "Type the amount plainly, like 1000 or 1.5." });
    expect(parse("usd", "0", true)).toEqual({ value: 0 });
    expect(parse("tok", "1e3")).toEqual({ error: "Enter a number above 0." });
    // r12 review: a leading point is a number; a cap in force re-saves unchanged.
    expect(parse("s-as-min", ".5")).toEqual({ value: 30 });
    expect(parse("min", ".5")).toEqual({ error: "Enter a whole number." });
    expect(parse("usd", raw("usd", 1.234))).toEqual({ value: 1.234 });
    expect(parse("min", "")).toEqual({ value: null });
    expect(parse("min", "0")).toEqual({ error: "Enter a number above 0." });
    expect(parse("s", "0", true)).toEqual({ value: 0 });
    expect(parse("min", "1.5")).toEqual({ error: "Enter a whole number." });
    expect(parse("s-as-min", "2")).toEqual({ value: 120 });
    expect(raw("s-as-min", 3600)).toBe("60");
  });
});

describe("a clean-up age", () => {
  it.each([["24H", "24h"], [" 2d ", "2d"], ["0h", "0h"]])("%s is sent as %s", (typed, sent) => {
    expect(parseAge(typed)).toEqual({ value: sent });
  });
  it("blank turns automatic clean-up off", () => {
    expect(parseAge("")).toEqual({ value: null });
  });
  it.each(["24", "1w", "90m", "1.5d"])("%s is refused with the fix", (typed) => {
    expect(parseAge(typed)).toEqual({ error: "Enter hours or days: 12h, 24h, 2d." });
  });
});

describe("a storage size", () => {
  it.each([["10g", "10G"], [" 1000M ", "1000M"], ["1T", "1T"], ["10GB", "10GB"]])("%s is sent as %s", (typed, sent) => {
    expect(parseSize(typed)).toEqual({ value: sent });
  });
  it("blank clears the key", () => {
    expect(parseSize("  ")).toEqual({ value: null });
  });
  it.each(["10", "1.5G", "0G", "ten"])("%s is refused with the fix", (typed) => {
    expect(parseSize(typed)).toEqual({ error: "Enter a whole number with a unit: 1000M, 10G, 1T." });
  });
  it("belongs to Housekeeping", () => {
    expect(sectionOfKey("storage.worktrees.limit")).toBe("housekeeping");
  });
});
