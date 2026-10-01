import { describe, expect, it } from "vitest";
import { weekBuckets } from "./weeks";

describe("weekBuckets", () => {
  it("is eight Mondays ending with the current UTC week, partial last, missing weeks 0", () => {
    // Wednesday 2026-09-30 22:00 UTC.
    const w = weekBuckets([{ week_start: "2026-09-28", n: 5 }, { week_start: "2026-09-14", n: 8 }], new Date("2026-09-30T22:00:00Z"));
    expect(w.map((x) => x.start)).toEqual(["2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]);
    expect(w.map((x) => x.n)).toEqual([0, 0, 0, 0, 0, 8, 0, 5]);
    expect(w.map((x) => x.partial)).toEqual([false, false, false, false, false, false, false, true]);
    expect(w[0].label).toBe("8/10");
  });

  it("reads the week in UTC, not in the machine's zone, just after a UTC Monday starts", () => {
    const w = weekBuckets([{ week_start: "2026-10-05", n: 3 }], new Date("2026-10-05T00:30:00Z"));
    expect(w[7]).toMatchObject({ start: "2026-10-05", n: 3, partial: true });
  });

  it("never reads the machine's local date parts", () => {
    // A Date whose local getters are wrong, as they are a few hours off UTC.
    class Skewed extends Date {
      getFullYear = () => 1999;
      getMonth = () => 0;
      getDate = () => 15;
      getDay = () => 3;
    }
    const w = weekBuckets([], new Skewed("2026-10-05T00:30:00Z"));
    expect(w[7].start).toBe("2026-10-05");
  });
});
