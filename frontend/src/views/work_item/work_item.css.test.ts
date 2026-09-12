import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

// jsdom doesn't apply imported stylesheets in this suite (styles.order.test.ts
// pins the same reasoning for the app-wide sheet), so these assert against
// the CSS source rather than a computed style.
describe("work_item.css · sticky pane headers (Kraft-6ap1)", () => {
  it("keeps the log header pinned", () => {
    const css = readFileSync(
      join(here, "../../styles.css"),
      "utf-8",
    );
    const rule = css.split(".log-head {")[1]?.split("}")[0] ?? "";
    expect(rule).toMatch(/position:\s*sticky/);
  });

  it("sits the tabs strip below the inspector head's own measured height, not a guessed 40px", () => {
    const css = readFileSync(join(here, "work_item.css"), "utf-8");
    expect(css).toMatch(/\.inspector \.tabs\s*\{[^}]*top:\s*var\(--inspector-head-h/);
  });
});
