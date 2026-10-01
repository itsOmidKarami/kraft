// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const cssFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : []));

/** Every class named anywhere in a selector (`.a .b:hover > .c`), comments and declarations removed. */
export function classes(text: string): string[] {
  const body = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@media[^{]*\{/g, "").replace(/\{[^{}]*\}/g, "\n");
  return [...body.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((m) => m[1]);
}
const foreign = (text: string) => [...new Set(classes(text).filter((c) => !c.startsWith("ph-") && !["btn", "btn-primary", "btn-secondary", "btn-danger"].includes(c)))];

describe("ng/phone CSS (W17 brief 0.1)", () => {
  it("names only ph- classes (and the shared Button's own, never styled on their own)", () => {
    const bad = cssFiles(here).flatMap((p) => foreign(readFileSync(p, "utf-8")).map((c) => `${relative(here, p)}: .${c}`));
    expect(cssFiles(here).length).toBeGreaterThan(0);
    expect(bad).toEqual([]);
  });

  it("catches a class without the prefix", () => {
    expect(foreign(".ph-a { x: 1 } .board-row .ph-b:hover, .menu-note { y: 2 }")).toEqual(["board-row", "menu-note"]);
  });

  it("is not styled by any other folder: no ng/ css outside phone names a ph- class", () => {
    const ng = join(here, "..");
    const others = cssFiles(ng).filter((p) => !p.startsWith(here));
    const bad = others.filter((p) => classes(readFileSync(p, "utf-8")).some((c) => c.startsWith("ph-")));
    expect(bad.map((p) => relative(ng, p))).toEqual([]);
  });
});
