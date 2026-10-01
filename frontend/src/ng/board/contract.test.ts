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
const offenders = (re: RegExp) => sources(here).filter((f) => re.test(readFileSync(f, "utf-8"))).map((f) => relative(here, f));

describe("board contract", () => {
  // R16 (W6 brief rule 4): groups, glyphs, tails and row actions read the
  // server's display_status and stop; the shipped board's derivation stays out.
  it("never imports deriveState, statusGroups or the shipped item states", () => {
    expect(offenders(/deriveState|statusGroups|useItemStates/)).toEqual([]);
  });

  // R17: cancelling keeps the branch and worktree; abandon deletes them and stays CLI-only.
  it("never calls /abandon", () => {
    expect(offenders(/\/abandon\b|abandonWorkItem/)).toEqual([]);
  });
});
