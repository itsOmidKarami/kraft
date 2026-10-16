// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { KraftEvent, WorkItem } from "../../types";
import { bannerOf, type BannerWords } from "./bannerWords";
import { age, eventLine } from "./events";

/** The web's half of `tests/console/wording_cases.json` for the Item: the
 *  stop banner and the event lines the console ports (`src/kraft/console/words.py`). */
type Cases = {
  now: string;
  chain: { nodes: { id: string; kind: string; artifact?: string }[] };
  banners: { name: string; item: Partial<WorkItem>; banner: BannerWords | null }[];
  events: { name: string; event: Omit<KraftEvent, "seq" | "work_item_id" | "created_at">; line: string | null }[];
  event_ages: [string | null, string][];
};
const CASES: Cases = JSON.parse(readFileSync(new URL("../../../../tests/console/wording_cases.json", import.meta.url), "utf-8"));

describe("the stop banner's words, as the console ports them", () => {
  it.each(CASES.banners.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(bannerOf(c.item as WorkItem)).toEqual(c.banner);
  });
});

describe("the Activity's event lines, as the console ports them", () => {
  it.each(CASES.events.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(eventLine({ seq: 1, work_item_id: "w", created_at: CASES.now, ...c.event } as KraftEvent, CASES.chain.nodes)).toBe(c.line);
  });

  it("dates an event as the console does", () => {
    const now = Date.parse(CASES.now);
    expect(CASES.event_ages.map(([iso]) => age(iso as string, now))).toEqual(CASES.event_ages.map(([, want]) => want));
  });
});
