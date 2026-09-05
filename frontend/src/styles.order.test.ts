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
