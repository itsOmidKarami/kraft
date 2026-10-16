// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ago, elapsed, until } from "../../format";
import type { WorkItem } from "../../types";
import { groupOf, sorter } from "./model";
import { chainMarks, reasonTail } from "./rowText";

/** The web's half of `tests/console/wording_cases.json`: the board wording the
 *  console ports (`src/kraft/console/words.py`, whose test runs the same cases). */
type Cases = {
  now: string;
  chain: { nodes: { id: string; kind: string }[] };
  rows: { name: string; row: Partial<WorkItem>; group: string; tail: string; marks?: string[] }[];
  order: { rows: Partial<WorkItem>[]; sorted: string[] };
  ago: [string | null, string][];
  until: [string | null, string][];
  elapsed: [number, string][];
};
const CASES: Cases = JSON.parse(readFileSync(new URL("../../../../tests/console/wording_cases.json", import.meta.url), "utf-8"));
const NOW = Date.parse(CASES.now);

describe("the board's wording, as the console ports it", () => {
  it.each(CASES.rows.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const row = { ...c.row, chain_definition: CASES.chain } as unknown as WorkItem;
    expect(groupOf(row)).toBe(c.group);
    expect(reasonTail(row, NOW)).toBe(c.tail);
    if (c.marks) expect(chainMarks(row)).toEqual(CASES.chain.nodes.map((n, k) => ({ node: n.id, gate: n.kind === "gate", state: c.marks![k] })));
  });

  it("sorts what needs you first, then the newest", () => {
    const rows = [...CASES.order.rows].sort(sorter("attention") as (a: Partial<WorkItem>, b: Partial<WorkItem>) => number);
    expect(rows.map((r) => r.id)).toEqual(CASES.order.sorted);
  });

  it.each([["ago", ago], ["until", until]] as const)("%s reads a time as the console does", (scale, said) => {
    expect(CASES[scale].map(([iso]) => said(iso, NOW))).toEqual(CASES[scale].map(([, want]) => want));
  });

  it("elapsed reads a duration as the console does", () => {
    expect(CASES.elapsed.map(([s]) => elapsed(s * 1000))).toEqual(CASES.elapsed.map(([, want]) => want));
  });
});
