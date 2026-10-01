// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
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

  it("shrinks the repo crumb before the work item title", () => {
    const css = readFileSync(join(here, "shell/shell.css"), "utf-8");
    const shrink = (sel: string) => Number(new RegExp(`${sel}\\s*{[^}]*flex-shrink:\\s*(\\d+)`).exec(css)?.[1]);
    expect(shrink("\\.ng-crumb-repo")).toBeGreaterThan(shrink("\\.ng-crumb-current"));
    expect(shrink("\\.ng-crumb-mid")).toBeGreaterThan(shrink("\\.ng-crumb-current"));
    expect(shrink("\\.ng-crumb-repo")).toBeGreaterThan(shrink("\\.ng-crumb-mid"));
  });

  // The one breakpoint ladder (shipped W2.1, spec §3). Here so it outlives the
  // shipped css.contract.test.ts, which the cutover deletes.
  it("uses only the 767 / 1023 / 1279 max-width queries and the 719 max-height query", () => {
    const allowed = new Set(["(max-width: 767px)", "(max-width: 1023px)", "(max-width: 1279px)", "(max-height: 719px)"]);
    const bad: string[] = [];
    for (const file of cssFiles(here)) {
      const css = readFileSync(file, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of css.matchAll(/@media\s*([^{]+)\{/g))
        if (!allowed.has(m[1].trim())) bad.push(`${relative(here, file)}: @media ${m[1].trim()}`);
    }
    expect(bad).toEqual([]);
  });
});

const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.(tsx?|css)$/.test(e.name) && !e.name.includes(".test.") ? [join(dir, e.name)] : [],
  );

describe("data-pan", () => {
  // The sweep's offscreen check exempts what a data-pan canvas clips (R41): only
  // the graph components may claim it, so it can't become a blanket mute.
  it("appears in no source file outside ng/graph", () => {
    const src = join(here, "..");
    const graph = join(here, "graph") + sep;
    const bad = sources(src).filter((f) => !f.startsWith(graph) && readFileSync(f, "utf-8").includes("data-pan"));
    expect(bad.map((f) => relative(src, f))).toEqual([]);
  });
});
