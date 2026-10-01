// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The phone is a separate app shape (W17 brief 0.2): it shares data hooks and
// pure models with the desktop, never a desktop pane. This list is the one door.
// A new shared module needs a line here and a reason.
const ALLOWED = [
  "ui/", // shared primitives, not restyled (R59)
  "theme/",
  "graph/NodeGlyph",
  "graph/types",
  "graph/layout", // the ChainNode type only
  "icons",
  "http",
  "live",
  "legacyPath", // the Soon placeholder's link to the shipped page
  "item/useItem",
  "item/useEvents",
  "item/useDocuments",
  "item/status",
  "item/actions",
  "item/url",
  "item/paths",
  "item/nodeGraph",
  "item/graph",
  "board/model",
  "board/rowText",
  "board/bulk",
  "board/prefs",
  "board/draft/draftBody",
  "review/useReview",
  "review/url",
  "review/finish",
  "review/patch",
  "review/model",
  "templates/draft/useConfigDraft",
  "templates/draft/draftApi",
  "templates/draft/types",
  "apply/store",
  "analytics/weeks",
];

const ng = join(dirname(fileURLToPath(import.meta.url)), "..");
const phone = join(ng, "phone") + sep;
const src = join(ng, "..");

const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !e.name.includes(".test.") ? [join(dir, e.name)] : [],
  );

/** Every relative import of a file that leaves `ng/phone` for another part of `ng/`, or reaches `deriveState`. */
export function violations(files: { path: string; text: string }[]): string[] {
  const bad: string[] = [];
  for (const f of files) {
    for (const m of f.text.matchAll(/(?:from|import)\s*\(?\s*["'](\.[^"']*)["']/g)) {
      const target = resolve(dirname(f.path), m[1]);
      const rel = relative(src, target);
      if (/(^|\/)deriveState$/.test(rel)) bad.push(`${relative(ng, f.path)}: ${m[1]} (deriveState)`);
      if (!target.startsWith(ng + sep) || target.startsWith(phone)) continue;
      const inNg = relative(ng, target).split(sep).join("/").replace(/\.(css|tsx?)$/, "");
      if (!ALLOWED.some((a) => (a.endsWith("/") ? inNg.startsWith(a) : inNg === a))) bad.push(`${relative(ng, f.path)}: ${m[1]}`);
    }
  }
  return bad;
}

describe("ng/phone imports", () => {
  it("take only data hooks, models and shared primitives from the rest of ng/, and never deriveState", () => {
    const files = sources(join(ng, "phone")).map((path) => ({ path, text: readFileSync(path, "utf-8") }));
    expect(files.length).toBeGreaterThan(5);
    expect(violations(files)).toEqual([]);
  });

  it("catches a desktop pane and deriveState, and lets a data hook through", () => {
    const at = join(ng, "phone", "x", "File.tsx");
    const text = (s: string) => [{ path: at, text: s }];
    expect(violations(text('import { StateCard } from "../../item/StateCard";'))).toHaveLength(1);
    expect(violations(text('import { Row } from "../../board/Row";'))).toHaveLength(1);
    expect(violations(text('import { deriveState } from "../../../deriveState";'))).toHaveLength(1);
    expect(violations(text('import { useItem } from "../../item/useItem";\nimport { Button } from "../../ui/Button";'))).toEqual([]);
  });
});

describe("the apply restart call", () => {
  it("has no call site in ng/phone yet (K adds the one, behind a confirm)", () => {
    const hits = sources(join(ng, "phone")).filter((f) => readFileSync(f, "utf-8").includes("apply/restart"));
    expect(hits.map((f) => relative(ng, f))).toEqual([]);
  });
});
