// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// /ng is one bundle, so every folder's CSS is global: a class this area styles
// at the head of a selector must not be one another folder styles too (W10:
// `.rv-diff`, `.pf` and `.menu-note` once restyled the review page and W1's menu).
const ng = join(dirname(fileURLToPath(import.meta.url)), "..");
const cssFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : []));

/** The class each selector of a stylesheet starts with (`.a.is-b > .c` → `a`). */
function heads(text: string): Set<string> {
  const out = new Set<string>();
  const body = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@media[^{]*\{/g, "").replace(/\{[^{}]*\}/g, "\n");
  for (const sel of body.split(/[\n,]/)) {
    const m = /^\s*\.([\w-]+)/.exec(sel);
    if (m) out.add(m[1]);
  }
  return out;
}

describe("Templates area CSS", () => {
  it("styles no class another /ng folder styles", () => {
    const mine = new Set<string>(), theirs = new Map<string, string>();
    for (const file of cssFiles(ng)) {
      const own = file.includes(`${join("ng", "templates")}`);
      for (const c of heads(readFileSync(file, "utf-8"))) own ? mine.add(c) : theirs.set(c, file.slice(ng.length + 1));
    }
    expect(mine.size).toBeGreaterThan(20);
    expect([...mine].filter((c) => theirs.has(c)).map((c) => `${c} (${theirs.get(c)})`)).toEqual([]);
  });
});
