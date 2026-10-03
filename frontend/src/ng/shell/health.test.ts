import { describe, expect, it } from "vitest";
import type { Health } from "../../types";
import { installedOlder, olderServer, restartPending } from "./health";

const h = (over: Partial<Health>): Health => ({ status: "ok", invalid_templates: {}, invalid_policy: [], ...over });

// R10c-01: a 1.4 or rc13 server sends `version` but never `installed`, and that is
// the window between 1.4's `kraft admin update` and the restart (doctor reads it so too).
describe("restartPending", () => {
  it.each([
    ["a server that predates `installed`", h({ version: "1.5.0rc13" }), true, true],
    ["a server running what is installed", h({ version: "2.0.0", installed: "2.0.0" }), false, false],
    ["a server running an older release than the one installed", h({ version: "2.0.0", installed: "2.0.1" }), true, false],
    ["a health with no version at all", h({}), false, false],
  ] as const)("%s", (_n, health, pending, older) => {
    expect(restartPending(health)).toBe(pending);
    expect(olderServer(health)).toBe(older);
  });

  it("is not pending before /health has answered", () => {
    expect(restartPending(null)).toBe(false);
    expect(olderServer(undefined)).toBe(false);
  });
});

// R10c-03: a rollback is not an update a restart finishes.
describe("installedOlder", () => {
  it.each([
    ["1.4.0", "2.0.0", true],
    ["1.5.0rc14", "2.0.0rc1", true],
    ["2.0.0rc1", "2.0.0", true],
    ["2.0.0", "2.0.0rc1", false],
    ["2.0.1", "2.0.0", false],
    ["0.0.0+source", "2.0.0", false],
  ])("%s installed under %s: %s", (installed, version, older) => {
    expect(installedOlder(h({ version, installed }))).toBe(older);
  });
});
