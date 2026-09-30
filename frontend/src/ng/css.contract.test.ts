// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const cssFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : [],
  );

// A colour written anywhere but the generated theme.css escapes the contrast
// tests and every surface × accent × amount × mode (W1 brief D.4).
const LITERAL =
  /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(|\b(?:white|black|red|green|blue|gray|grey|yellow|orange|purple|pink|silver|canvas|canvastext|buttonface|buttontext|highlight)\b/i;

describe("ng CSS", () => {
  it("writes no colour literal outside theme.css", () => {
    const bad: string[] = [];
    for (const file of cssFiles(here)) {
      if (file.endsWith("/theme/theme.css")) continue;
      const css = readFileSync(file, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of css.matchAll(/:\s*([^;{}]+)[;}]/g))
        if (LITERAL.test(m[1])) bad.push(`${relative(here, file)}: ${m[1].trim()}`);
    }
    expect(bad).toEqual([]);
  });
});
