// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ago, until } from "../../format";
import type { WorkItem } from "../../types";
import { groupOf, sorter } from "./model";
import { reasonTail } from "./rowText";

/** The web's half of `tests/console/wording_cases.json`: the board wording the
 *  console ports (`src/kraft/console/words.py`, whose test runs the same cases). */
type Cases = {
  now: string;
  rows: { name: string; row: Partial<WorkItem>; group: string; tail: string }[];
  order: { rows: Partial<WorkItem>[]; sorted: string[] };
  ago: [string | null, string][];
  until: [string | null, string][];
};
const CASES: Cases = JSON.parse(readFileSync(new URL("../../../../tests/console/wording_cases.json", import.meta.url), "utf-8"));
const NOW = Date.parse(CASES.now);

describe("the board's wording, as the console ports it", () => {
  it.each(CASES.rows.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const row = c.row as WorkItem;
    expect(groupOf(row)).toBe(c.group);
    expect(reasonTail(row, NOW)).toBe(c.tail);
  });

  it("sorts what needs you first, then the newest", () => {
    const rows = [...CASES.order.rows].sort(sorter("attention") as (a: Partial<WorkItem>, b: Partial<WorkItem>) => number);
    expect(rows.map((r) => r.id)).toEqual(CASES.order.sorted);
  });

  it.each([["ago", ago], ["until", until]] as const)("%s reads a time as the console does", (scale, said) => {
    expect(CASES[scale].map(([iso]) => said(iso, NOW))).toEqual(CASES[scale].map(([, want]) => want));
  });
});
