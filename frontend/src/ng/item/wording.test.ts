// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../types";
import { bannerOf, type BannerWords } from "./bannerWords";

/** The web's half of `tests/console/wording_cases.json` for the Item: the
 *  stop banner the console ports (`src/kraft/console/words.py`). */
type Cases = {
  banners: { name: string; item: Partial<WorkItem>; banner: BannerWords | null }[];
};
const CASES: Cases = JSON.parse(readFileSync(new URL("../../../../tests/console/wording_cases.json", import.meta.url), "utf-8"));

describe("the stop banner's words, as the console ports them", () => {
  it.each(CASES.banners.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(bannerOf(c.item as WorkItem)).toEqual(c.banner);
  });
});
