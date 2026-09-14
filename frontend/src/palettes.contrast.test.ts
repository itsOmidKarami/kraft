import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * W1.2: every text token × every palette × both modes against its surface,
 * read from the CSS the app ships (nocturne.css → palettes.css → styles.css
 * :root), resolving `var()` the way the cascade would for that palette/mode.
 */
const here = dirname(fileURLToPath(import.meta.url));
const read = (f: string) => readFileSync(join(here, f), "utf-8");
const blocks = (css: string) =>
  new Map(
    [...css.matchAll(/(:root[^{]*)\{([^}]*)\}/g)].map((m) => [
      m[1].trim(),
      Object.fromEntries([...m[2].matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((d) => [d[1], d[2].trim()])),
    ]),
  );
const noct = blocks(read("nocturne.css"));
const pal = blocks(read("palettes.css"));
const kraft = blocks(read("styles.css"));

// Lowest to highest precedence, as the cascade orders them.
function tokens(palette: string, mode: string): Record<string, string> {
  return {
    ...noct.get(":root"),
    ...kraft.get(":root"),
    ...pal.get(`:root[data-palette="${palette}"]`),
    ...pal.get(`:root[data-mode="${mode}"]`),
    ...pal.get(`:root[data-palette="${palette}"][data-mode="${mode}"]`),
  };
}
function resolve(t: Record<string, string>, name: string, depth = 0): string {
  const v = t[name];
  if (v === undefined) throw new Error(`${name} undefined`);
  const m = v.match(/^var\((--[a-z0-9-]+)\)$/);
  if (m) return depth > 8 ? v : resolve(t, m[1], depth + 1);
  if (!/^#[0-9a-f]{6}$/i.test(v)) throw new Error(`${name} is not a literal colour: ${v}`);
  return v;
}
const lum = (h: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

const PALETTES = ["nocturne", "rose", "forest", "amber", "slate"];
const STATUS = ["needs-you", "running", "done", "capped", "budget"];
// Ramp steps that styles.css uses as text colour.
const TEXT_STEPS = ["neutral-100", "neutral-200", "neutral-300", "neutral-400", "neutral-500", "accent-100", "accent-200", "accent-300", "accent-400"];

describe.each(PALETTES)("palette %s", (palette) => {
  it.each(["dark", "light"])("%s: text and muted hold 4.5:1, faint 3:1, on bg and surface", (mode) => {
    const t = tokens(palette, mode);
    for (const ground of ["--color-bg", "--color-surface"]) {
      const g = resolve(t, ground);
      expect(contrast(resolve(t, "--color-text"), g), `text on ${ground}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(resolve(t, "--color-text-muted"), g), `muted on ${ground}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(resolve(t, "--color-text-faint"), g), `faint on ${ground}`).toBeGreaterThanOrEqual(3);
    }
  });

  // W10.A: dark mode's small text on its three grounds -- the page, a card, and
  // the selected-row fill (accent-900) the tree, documents and chains lists paint.
  it("dark: muted, neutral-400 and accent-400 hold 4.5:1 on bg, surface and the selected-row fill", () => {
    const t = tokens(palette, "dark");
    for (const tok of ["--color-text-muted", "--color-neutral-400", "--color-accent-400"])
      for (const ground of ["--color-bg", "--color-surface", "--color-accent-900"])
        expect(contrast(resolve(t, tok), resolve(t, ground)), `${tok} on ${ground}`).toBeGreaterThanOrEqual(4.5);
  });

  it.each(["dark", "light"])("%s: the bottom-nav badge's ground ink holds 4.5:1 on the accent fill", (mode) => {
    const t = tokens(palette, mode);
    expect(contrast(resolve(t, "--color-bg"), resolve(t, "--color-accent"))).toBeGreaterThanOrEqual(4.5);
  });

  it("light: every ramp step used as text holds 4.5:1 on bg and surface", () => {
    const t = tokens(palette, "light");
    for (const s of TEXT_STEPS)
      for (const ground of ["--color-bg", "--color-surface"])
        expect(contrast(resolve(t, `--color-${s}`), resolve(t, ground)), `${s} on ${ground}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(resolve(t, "--color-accent"), resolve(t, "--color-surface")), "accent").toBeGreaterThanOrEqual(4.5);
  });

  it("light: status tints hold 3:1 as fills and 4.5:1 as text on the surface, ink 4.5:1 on each", () => {
    const t = tokens(palette, "light");
    const surface = resolve(t, "--color-surface");
    for (const s of STATUS) {
      const fill = resolve(t, `--color-status-${s}`);
      expect(contrast(fill, surface), `${s} fill`).toBeGreaterThanOrEqual(3);
      expect(contrast(fill, surface), `${s} as text`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(resolve(t, "--color-status-ink"), fill), `ink on ${s}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("light block restates the whole role set, not text alone", () => {
    const own = pal.get(`:root[data-palette="${palette}"][data-mode="light"]`) ?? {};
    const want = ["bg", "surface", "surface-2", "text", "text-muted", "text-faint", "border", "accent", "accent-2", "neutral-700", "neutral-800", "neutral-900", ...STATUS.map((s) => `status-${s}`), "status-ink"];
    expect(want.filter((k) => !(`--color-${k}` in own))).toEqual([]);
  });
});
