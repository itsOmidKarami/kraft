// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const sources = [...readdirSync(here), ...readdirSync(join(here, "..")).filter((f) => f === "PolicyPage.tsx").map((f) => join("..", f))]
  .filter((f) => /\.tsx?$/.test(f) && !f.includes(".test.") && !f.includes("fixture"));

describe("Policy's scope model is the server's", () => {
  // The page draws the bound, the binding cap, a value's source and a loop's winning layer from `resolved`; it never works them out (W15 exit).
  it("computes no bound in the page's code", () => {
    const bad = sources.filter((f) => /Math\.(min|max)\b|\bnearest\(|\bboundOf\b|\bbound\(/.test(readFileSync(join(here, f), "utf-8")));
    expect(bad).toEqual([]);
  });

  it("reads each cap's maximum and its level from the answer", () => {
    const limits = readFileSync(join(here, "Limits.tsx"), "utf-8");
    expect(limits).toContain("c.maximum.value");
    expect(limits).toContain("c.maximum.source");
  });
});
