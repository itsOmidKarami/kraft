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

  it("takes an unpinned sidebar to no width, and keeps its overlay 180ms after the pointer leaves", () => {
    const css = readFileSync(join(here, "shell/shell.css"), "utf-8");
    expect(css).toMatch(/:root\[data-sidebar="rail"\]\s*{\s*--ng-side-w:\s*0px;/);
    expect(css).toMatch(/:root\[data-sidebar="rail"\] \.ng-sidebar\s*{[^}]*transition:[^;}]*\b180ms\b/);
  });

  it("lays the document drawer over the theme's scrim, black at 40%", () => {
    expect(readFileSync(join(here, "theme/theme.css"), "utf-8")).toContain(":root { --scrim: rgb(0 0 0 / 40%); }");
    expect(readFileSync(join(here, "item/item.css"), "utf-8")).toMatch(/\.dv-scrim\s*{[^}]*background:\s*var\(--scrim\)/);
  });

  it("scrolls a pane's log in its own <pre>, which fills the pane body, so follow has something to move", () => {
    const css = readFileSync(join(here, "item/item.css"), "utf-8");
    expect(css).toMatch(/\.ip-log-wrap\s*{[^}]*height:\s*100%/);
    expect(css).toMatch(/\.ip-log-wrap > \.ip-log\s*{[^}]*overflow:\s*auto/);
  });

  it("keeps the review footer's buttons on one line, the summary giving way", () => {
    const css = readFileSync(join(here, "review/review.css"), "utf-8");
    expect(css).toMatch(/\.rv-bar \.btn\s*{[^}]*flex:\s*none;[^}]*white-space:\s*nowrap/);
    expect(css).toMatch(/\.rv-bar > \.rv-muted\s*{[^}]*text-overflow:\s*ellipsis/);
  });

  it("stacks a dialog under a popover (a menu opened in it) under a toast", () => {
    const css = readFileSync(join(here, "ui/ui.css"), "utf-8");
    const z = (sel: string) => Number(new RegExp(`^${sel}\\s*{[^}]*z-index:\\s*(\\d+)`, "m").exec(css)?.[1]);
    expect(z("\\.dialog-backdrop")).toBeLessThan(z("\\.popover"));
    expect(z("\\.popover")).toBeLessThan(z("\\.toasts"));
  });

  // R12b-07 (fixed by #502's header): a fixed 118px wrapped "Raise cap", "Review conflicts" and "Reopen MR" and clipped them.
  it("lets the item header's main button grow with its label, on one line", () => {
    const css = readFileSync(join(here, "item/item.css"), "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = (sel: string) => new RegExp(`(?:^|\\n)${sel}\\s*{([^}]*)}`).exec(css)?.[1] ?? "";
    expect(rule("\\.item-main")).not.toMatch(/(?:^|[;\s])width:/);
    expect(rule("\\.item-main")).toMatch(/min-width:\s*118px/);
    expect(rule("\\.item-main-label")).toMatch(/white-space:\s*nowrap/);
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

describe("landmarks", () => {
  // The shell's <main id="ng-main"> is the skip link's target; a page inside it that renders its own makes two.
  // The sign-in card and the unlinked gallery sit outside the shell, so each has the page's only one; the phone app has its own
  // frame (one <main> around every screen) and its own sign-in card.
  it("renders <main> only in the shell, the sign-in card, the gallery and the phone app's frame and sign-in card", () => {
    const own = ["shell/Shell.tsx", "shell/SignIn.tsx", "graph/Gallery.tsx", "phone/PhoneApp.tsx", "phone/signin/PhoneSignIn.tsx"].map((f) => join(here, f));
    const bad = sources(here).filter((f) => f.endsWith(".tsx") && !own.includes(f) && /<main[\s>]/.test(readFileSync(f, "utf-8")));
    expect(bad.map((f) => relative(here, f))).toEqual([]);
  });
});
