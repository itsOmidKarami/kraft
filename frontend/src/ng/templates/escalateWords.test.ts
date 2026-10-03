import { describe, expect, it } from "vitest";
import { autoEscalates } from "./escalateWords";

describe("autoEscalates", () => {
  it.each([
    [0, "at once"],
    [30, "after 30s"],
    [60, "after 1m"],
    [600, "after 10m"],
  ])("%ss reads %s", (s, words) => expect(autoEscalates(s)).toBe(words));
});
