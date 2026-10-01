// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SCREENS } from "./screens";

const app = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "PhoneApp.tsx"), "utf-8");
/** The routes `PhoneApp` answers with a screen: not a redirect, an alias or the catch-alls. */
const routes = app
  .split("\n")
  .map((line) => /<Route path="([^"]+)" element=(.*)$/.exec(line))
  .filter((m): m is RegExpExecArray => !!m && m[1] !== "*" && !/Navigate|Alias|NotFound/.test(m[2]))
  .map((m) => m[1]);

describe("the phone's screen list (P.1)", () => {
  it("names every route PhoneApp answers, and every entry's route is one", () => {
    expect([...new Set(SCREENS.map((s) => s.route))].sort()).toEqual([...new Set(routes)].sort());
  });

  it("has one id per screen, each reached from the board by at least one tap unless it is the board", () => {
    expect(new Set(SCREENS.map((s) => s.id)).size).toBe(SCREENS.length);
    for (const s of SCREENS) expect(s.taps.length > 0 || s.id === "board", s.id).toBe(true);
  });

  it("marks a screen as needing data exactly when it has no fixed heading", () => {
    for (const s of SCREENS) expect(!s.heading, s.id).toBe(!!s.data);
  });
});
