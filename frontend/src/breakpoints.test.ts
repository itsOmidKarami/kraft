import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/** W2.1: one breakpoint ladder for every stylesheet — phone 767, tablet 1023,
 *  narrow 1279, plus the one short-height rule. A stray `640` or `min-width`
 *  query is how the same component used to switch layout at two widths. */
const ALLOWED = new Set(["(max-width: 767px)", "(max-width: 1023px)", "(max-width: 1279px)", "(max-height: 719px)"]);

const here = dirname(fileURLToPath(import.meta.url));

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : [],
  );
}

describe("CSS breakpoints (W2.1)", () => {
  it("uses only the 767 / 1023 / 1279 max-width queries and the 719 max-height query", () => {
    const bad: string[] = [];
    for (const file of cssFiles(here)) {
      // Comments may name old breakpoints; only real at-rules count.
      const css = readFileSync(file, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of css.matchAll(/@media\s*([^{]+)\{/g)) {
        const query = m[1].trim();
        if (!ALLOWED.has(query)) bad.push(`${file.slice(here.length + 1)}: @media ${query}`);
      }
    }
    expect(bad).toEqual([]);
  });
});
