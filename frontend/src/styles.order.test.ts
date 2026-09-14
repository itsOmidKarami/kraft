import { readFileSync } from "node:fs";
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

describe("page stylesheet overrides", () => {
  it("gives the plugins grid a specificity that beats .template-editor, and chains no longer sits on it", () => {
    const chains = readFileSync(join(here, "views/settings/templates.css"), "utf-8");
    const plugins = readFileSync(join(here, "views/settings/plugins.css"), "utf-8");
    // W11 · D: the chains editor is one card, not a .template-editor grid.
    expect(chains).not.toMatch(/\.template-editor/);
    expect(plugins).toMatch(/\.template-editor\.plugins-editor\s*\{[^}]*grid-template-columns/);
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
