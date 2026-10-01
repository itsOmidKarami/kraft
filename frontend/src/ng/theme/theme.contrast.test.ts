// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * The floors tests/test_theme_contrast.py holds the generator to, read from the
 * theme.css the new UI ships, so the file and dev/gen_theme.py cannot drift.
 */
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "theme.css"), "utf-8");
const blocks = new Map(
  [...css.matchAll(/^(\[[^{]*)\{([^}]*)\}/gm)].map((m) => [
    m[1].trim(),
    Object.fromEntries([...m[2].matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((d) => [d[1], d[2].trim()])),
  ]),
);

const SURFACES = ["graphite", "slate", "ink", "sand", "moss"];
const MODES = ["dark", "light"];
const AMOUNTS = ["mono", "subtle", "full"];
const ACCENTS = ["none", "blue", "violet", "green", "amber", "rose"];

function tokens(s: string, m: string, a: string, x: string): Record<string, string> {
  const t = blocks.get(`[data-surface="${s}"][data-mode="${m}"][data-amount="${a}"]`);
  const acc = blocks.get(`[data-accent="${x}"][data-mode="${m}"][data-amount="${a}"]`);
  if (!t || !acc) throw new Error(`no block for ${s}/${m}/${a}/${x}`);
  const all = { ...t, ...acc };
  for (const [k, v] of Object.entries(all)) {
    const ref = v.match(/^var\((--[a-z0-9-]+)\)$/);
    if (ref) all[k] = t[ref[1]];
    if (!/^#[0-9a-f]{6}$/.test(all[k])) throw new Error(`${k} is not a literal colour: ${v}`);
  }
  return all;
}
const lum = (h: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

// 5 surfaces × 2 modes × 13 amount-and-accent choices (Mono locks the accent to none).
const combos = SURFACES.flatMap((s) =>
  MODES.flatMap((m) => AMOUNTS.flatMap((a) => (a === "mono" ? ["none"] : ACCENTS).map((x) => [s, m, a, x]))),
);

const SCHEMES: [string, string][] = [["solarized-light", "light"], ["solarized-dark", "dark"], ["monokai", "dark"], ["dracula", "dark"]];

describe("theme.css code schemes", () => {
  it.each(SCHEMES)("%s (%s) tokens clear 4.5 on every ground", (id, m) => {
    const t = blocks.get(`[data-code="${id}"][data-mode="${m}"]`);
    expect(t, "scheme block").toBeDefined();
    for (const role of ["kw", "str", "num", "com", "name"]) {
      for (const s of SURFACES) for (const a of AMOUNTS) for (const g of ["--bg", "--surface"]) {
        const ground = blocks.get(`[data-surface="${s}"][data-mode="${m}"][data-amount="${a}"]`)![g];
        expect(contrast(t![`--tok-${role}`], ground), `${role} on ${s}/${a} ${g}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});

describe("theme.css", () => {
  it("has 30 neutral blocks and 36 accent blocks", () => {
    expect([...blocks.keys()].filter((k) => k.startsWith("[data-surface")).length).toBe(30);
    expect([...blocks.keys()].filter((k) => k.startsWith("[data-accent")).length).toBe(36);
    expect(combos.length).toBe(130);
  });

  it.each(combos)("%s %s %s accent %s clears every floor", (s, m, a, x) => {
    const t = tokens(s, m, a, x);
    const at = (fg: string, bg: string, floor: number) =>
      expect(contrast(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(floor);
    for (const g of ["--bg", "--side", "--surface"]) {
      for (const r of ["--text", "--text-sub", "--text-muted"]) at(r, g, 4.5);
      at("--text-faint", g, 3);
    }
    for (const g of ["--bg", "--surface"]) at("--accent", g, 4.5);
    for (const r of ["--ok", "--warn", "--bad", "--info"]) {
      at(r, "--bg", 3);
      at(r, "--surface", 4.5);
    }
    at("--focus", "--bg", 3);
  });
});

describe("review.css scheme hook", () => {
  // A named scheme only reaches the diff through these (W16 D.6); the fallback keeps `auto` as it was.
  const review = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../review/review.css"), "utf-8");
  it.each([["kw", "info"], ["str", "ok"], ["num", "warn"], ["com", "text-muted"], ["name", "text"]])(".tok-%s reads --tok-%s fallback", (k, fb) => {
    expect(review).toContain(`.tok-${k} { color: var(--tok-${k}, var(--${fb}));`);
  });
});
