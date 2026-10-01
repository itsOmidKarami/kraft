// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : [],
  );

describe("item page contract", () => {
  // R16, R27, R28: the badge, main button, banner and card read the server's
  // display_status and stop; the shipped UI's client-side derivation stays out.
  it("never imports deriveState or the shipped item states", () => {
    const bad = sources(here).filter((f) => /from\s+["'][./]*(?:deriveState|store["'].*useItemStates)|useItemStates|deriveState/.test(readFileSync(f, "utf-8")));
    expect(bad.map((f) => relative(here, f))).toEqual([]);
  });

  // R17: cancelling keeps the branch and worktree; abandon deletes them and stays CLI-only.
  it("never calls /abandon", () => {
    const bad = sources(here).filter((f) => /abandon/i.test(readFileSync(f, "utf-8")));
    expect(bad.map((f) => relative(here, f))).toEqual([]);
  });
});
