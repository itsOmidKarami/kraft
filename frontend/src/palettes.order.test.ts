import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

// `--color-accent`/`--color-accent-2` are set both in a ramp block
// (`[data-palette="X"]`) and in the light mode block
// (`[data-mode="light"]`); both selectors tie on specificity, so which
// one wins is decided by source order alone. Light mode's override has
// to come LAST or it silently loses on every non-Nocturne palette (see
// the comment at the top of palettes.css).
describe("palettes.css cascade order", () => {
  it("declares [data-mode=\"light\"] after every ramp block", () => {
    const css = readFileSync(join(here, "palettes.css"), "utf-8");

    const modeLightPos = css.indexOf(':root[data-mode="light"] {');
    expect(modeLightPos).toBeGreaterThan(-1);

    for (const palette of ["rose", "forest", "amber", "slate"]) {
      const rampPos = css.indexOf(`:root[data-palette="${palette}"] {`);
      expect(rampPos, `${palette} ramp block should exist`).toBeGreaterThan(-1);
      expect(modeLightPos).toBeGreaterThan(rampPos);
    }
  });
});
