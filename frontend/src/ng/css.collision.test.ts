// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The UI is one bundle, so every folder's CSS is global (R59). A class styled at
// the head of a selector belongs to the one top-level folder that styles it;
// ui/ and theme/ are shared: anyone may use their classes, only they style them.
// W10 found `.rv-diff`, `.pf` and `.menu-note` styled by two folders, which moved
// W8's review diff by 12px.
const here = dirname(fileURLToPath(import.meta.url));
const cssFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : []));

/** The class each selector of a stylesheet starts with (`.a.is-b > .c` → `a`). */
export function heads(text: string): Set<string> {
  const out = new Set<string>();
  const body = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@media[^{]*\{/g, "").replace(/\{[^{}]*\}/g, "\n");
  for (const sel of body.split(/[\n,]/)) {
    const m = /^\s*\.([\w-]+)/.exec(sel);
    if (m) out.add(m[1]);
  }
  return out;
}

/** Classes styled at a selector's head, by the top-level folder (`board`, `review`, …; `.` for ng/ itself). */
export function owners(files: { path: string; text: string }[]): Map<string, Set<string>> {
  const by = new Map<string, Set<string>>();
  for (const f of files) {
    const folder = f.path.includes(sep) ? f.path.split(sep)[0] : ".";
    for (const c of heads(f.text)) {
      if (!by.has(c)) by.set(c, new Set());
      by.get(c)!.add(folder);
    }
  }
  return by;
}

/** Each class more than one folder styles, with the folders. */
export const collisions = (by: Map<string, Set<string>>) =>
  [...by].filter(([, fs]) => fs.size > 1).map(([c, fs]) => `${c}: ${[...fs].sort().join(", ")}`).sort();

describe("ng CSS ownership", () => {
  it("styles every class from one top-level folder only", () => {
    const files = cssFiles(here).map((p) => ({ path: relative(here, p), text: readFileSync(p, "utf-8") }));
    expect(files.length).toBeGreaterThan(8);
    expect(collisions(owners(files))).toEqual([]);
  });

  it("catches a class styled by two folders, in either direction", () => {
    const shared = owners([{ path: join("ui", "ui.css"), text: ".menu-note { color: x }" }, { path: join("templates", "t.css"), text: ".tpl-a, .menu-note.is-b > .c { color: y }" }]);
    expect(collisions(shared)).toEqual(["menu-note: templates, ui"]);
    const peers = owners([{ path: join("review", "r.css"), text: "@media (max-width: 767px) { .rv-diff { x: 1 } }" }, { path: join("templates", "t.css"), text: ".rv-diff { x: 2 }" }]);
    expect(collisions(peers)).toEqual(["rv-diff: review, templates"]);
  });
});
