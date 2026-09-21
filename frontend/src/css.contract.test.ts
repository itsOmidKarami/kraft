// @vitest-environment node
// Pins on stylesheet source text, in one file (Wave 4). jsdom applies no
// imported CSS and has no viewport, so cascade order, breakpoints and a few
// load-bearing declarations are asserted against the CSS source. Real layout
// belongs in Playwright; do not add layout-by-regex pins here.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

// jsdom has no viewport, so a `@media (max-width: ...)` rule cannot be
// exercised by rendering -- there is no way to assert its effect from a
// test in this suite. Source order, unlike the viewport, is plain text: for
// two rules of equal specificity (a media query adds none), the one that
// appears LATER in the stylesheet wins the cascade. An override block placed
// earlier than the base rule it means to override is not wrong, it is inert
// -- exactly the bug this pins: the phone diff-viewer overrides were
// originally declared before the unconditional `.diff-modal`/`.diff-body`
// base rules and were silently dead, because the base rules came later and
// won every time regardless of the media query.
describe("styles.css cascade order", () => {
  it("declares the phone .diff-body override after the base rule it overrides", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");

    const basePos = css.indexOf(".diff-body > div { white-space: pre; }");
    const overridePos = css.indexOf(".diff-body > div { white-space: pre-wrap;");

    expect(basePos).toBeGreaterThan(-1);
    expect(overridePos).toBeGreaterThan(-1);
    expect(overridePos).toBeGreaterThan(basePos);
  });

  // Was e2e phone.visual "the document viewer hides what a phone cannot do",
  // which CI always skipped (no linked doc at the spec gate). The wrapper's
  // `display: flex` outranks `.desktop-only { display: none }`, so the phone
  // block has to hide it again with the same selector, later in the file.
  it("re-hides the document pane's editor controls inside the phone block", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = ".doc-modal-actions > .desktop-only {";
    const base = css.indexOf(`${rule} display: flex`);
    const phone = css.indexOf(`${rule} display: none; }`);
    expect(base).toBeGreaterThan(-1);
    expect(phone).toBeGreaterThan(base);
    // The innermost @media still open at `phone` must be the phone query.
    const media = css.lastIndexOf("@media", phone);
    expect(css.slice(media, css.indexOf("{", media)).trim()).toBe("@media (max-width: 767px)");
    const between = css.slice(media, phone);
    expect(between.split("{").length - between.split("}").length).toBe(1);
  });
});

// Specificity, not order: `textarea.input { min-height: 90px }` in
// nocturne.css is one element + one class, which outranks a bare
// `.template-yaml` no matter which file is imported first. The two sibling
// rules in this file that do work (`.gate-reject textarea.input`,
// `.paused-card textarea.input`) both qualify with the element. Asserted
// against the source rather than a computed style for the same reason the
// block above is: jsdom's cascade is not a thing to build a regression test
// on, and the failure mode here is exactly a selector that lost.
describe("styles.css specificity", () => {
  it("qualifies .template-yaml with the element so textarea.input cannot outrank it", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");

    const rule = css
      .split("\n")
      .find((line) => line.includes(".template-yaml") && line.includes("min-height"));

    expect(rule).toBeDefined();
    expect(rule).toMatch(/textarea\.template-yaml/);
  });
});

// Kraft-9fj8: ES imports execute depth-first in source order, so App's whole
// import graph (every views/**/*.css page stylesheet) runs before whatever
// main.tsx imports after App. styles.css used to be one of those "after"
// imports, making it the last stylesheet bundled -- it won every equal-
// specificity tie against a page rule, silently. jsdom can't exercise the
// cascade itself (no layout engine), so this pins the one thing that can
// regress it back: the source order of the two import lines.
describe("main.tsx import order", () => {
  it("imports styles.css before App, so App's page stylesheets are bundled first", () => {
    const main = readFileSync(join(here, "main.tsx"), "utf-8");

    const stylesPos = main.indexOf('import "./styles.css"');
    const appPos = main.indexOf('import { App } from "./App"');

    expect(stylesPos).toBeGreaterThan(-1);
    expect(appPos).toBeGreaterThan(-1);
    expect(stylesPos).toBeLessThan(appPos);
  });
});

describe("Hairline utility", () => {
  it("defines --hairline-24/--hairline-48 and exposes them as .hairline/.hairline-section", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");
    expect(css).toMatch(/--hairline-24:/);
    expect(css).toMatch(/--hairline-48:/);
    expect(css).toMatch(/\.hairline\s*,?\s*[^{]*\{\s*background:\s*var\(--hairline-24\);?\s*\}/);
    expect(css).toMatch(/\.hairline-section\s*\{\s*background:\s*var\(--hairline-48\);?\s*\}/);
  });

  it("points .row's default background at the shared --hairline-48 token", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");
    const rowRule = css.split(".row {")[1]?.split("}")[0] ?? "";
    expect(rowRule).toMatch(/var\(--hairline-48\)/);
  });
});

describe("phone inputs (W14 · C.3)", () => {
  // A field's own size is a class (`.input` 14px), which beats a bare type
  // selector: without `!important` the phone 16px rule was inert on 28 cells.
  it("sets every phone input, select and textarea to 16px over their classes", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");
    expect(css).toMatch(/input, select, textarea \{ font-size: 16px !important; \}/);
  });
});

/** W2.1: one breakpoint ladder for every stylesheet — phone 767, tablet 1023,
 *  narrow 1279, plus the one short-height rule. A stray `640` or `min-width`
 *  query is how the same component used to switch layout at two widths. */
const ALLOWED = new Set(["(max-width: 767px)", "(max-width: 1023px)", "(max-width: 1279px)", "(max-height: 719px)"]);


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

// jsdom doesn't apply imported stylesheets in this suite (the styles.css
// order tests above pin the same reasoning), so these assert against
// the CSS source rather than a computed style.
describe("work_item.css · sticky pane headers (Kraft-6ap1)", () => {
  it("keeps the log header pinned", () => {
    const css = readFileSync(
      join(here, "styles.css"),
      "utf-8",
    );
    const rule = css.split(".log-head {")[1]?.split("}")[0] ?? "";
    expect(rule).toMatch(/position:\s*sticky/);
  });

  it("sits the tabs strip below the inspector head's own measured height, not a guessed 40px", () => {
    const css = readFileSync(join(here, "views/work_item/work_item.css"), "utf-8");
    expect(css).toMatch(/\.inspector \.tabs\s*\{[^}]*top:\s*var\(--inspector-head-h/);
  });
});

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
