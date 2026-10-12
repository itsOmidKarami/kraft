import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { MUTATING, guarded, mutating } from "../../src/core/guard";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));
const menus = Object.values(pkg.contributes.menus as Record<string, { command: string; when?: string }[]>).flat();
const hiddenWhenReadOnly = new Set(menus.filter((m) => m.when?.includes("!kraft.readOnly")).map((m) => m.command));

describe("MUTATING", () => {
  it.each([...hiddenWhenReadOnly, "kraft.startDaemon", "kraft.review.submit"])("guards %s", (id) =>
    expect(MUTATING.has(id)).toBe(true));
  it("names only declared commands", () => {
    const declared = new Set((pkg.contributes.commands as { command: string }[]).map((c) => c.command));
    expect([...MUTATING].filter((id) => !declared.has(id))).toEqual([]);
  });
});

describe("guarded", () => {
  it.each([
    { id: "refused", no: "Kraft is read-only.", ran: 0, warned: ["Kraft is read-only."] },
    { id: "allowed", no: null, ran: 1, warned: [] },
  ])("$id", ({ no, ran, warned }) => {
    const run = vi.fn((x: number) => x + 1);
    const warn = vi.fn();
    const out = guarded(() => no, warn, run)(1);
    expect(run).toHaveBeenCalledTimes(ran);
    expect(warn.mock.calls.map((c) => c[0])).toEqual(warned);
    expect(out).toBe(ran ? 2 : undefined);
  });
});

describe("mutating", () => {
  it("refuses to register an id MUTATING does not name", () =>
    expect(() => mutating("kraft.openInWebUi", () => null, () => {}, () => {})).toThrow(/not in MUTATING/));
});
