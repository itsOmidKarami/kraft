import { describe, expect, it } from "vitest";
import { acceptHealth, downRow, onceEach, statusMenu, statusTooltip } from "../../src/core/health";

describe("health", () => {
  it.each([
    [null, "Kraft isn't running — Start", "kraft.startDaemon"],
    [{ status: 0, detail: "x" }, "Kraft isn't running — Start", "kraft.startDaemon"],
    [{ status: 401, detail: "x" }, "Kraft refused the token — check kraft.url", "workbench.action.openSettings"],
    [{ status: 403, detail: "x" }, "Kraft refused the token — check kraft.url", "workbench.action.openSettings"],
    [{ status: 500, detail: "db locked" }, "Kraft error: db locked — Show log", "kraft.showLog"],
    [{ status: -1, detail: "SyntaxError" }, "Kraft error: SyntaxError — Show log", "kraft.showLog"],
  ])("downRow(%o)", (err, label, command) => expect(downRow(err)).toMatchObject({ label, command }));

  it("opens settings at kraft.url", () => expect(downRow({ status: 401, detail: "" }).args).toEqual(["kraft.url"]));

  it("offers Start only when down", () => {
    expect(statusMenu(false).map((m) => m.label)).toEqual(["Show Log", "Open Web UI", "Start Kraft", "Retry Now"]);
    expect(statusMenu(true).map((m) => m.label)).toEqual(["Show Log", "Open Web UI", "Retry Now"]);
    expect(statusMenu(false).map((m) => m.choice)).toEqual(["log", "web", "start", "retry"]);
  });

  it("onceEach says yes once per key", () => {
    const once = onceEach();
    expect([once("a"), once("a"), once("b")]).toEqual([true, false, true]);
  });

  it.each([
    ["down with a refused token", false, undefined, { status: 401, detail: "x" }, "Kraft refused the token — check kraft.url"],
    ["down and unreachable", false, undefined, { status: 0, detail: "x" }, "Kraft isn't running — Start"],
    ["reconnecting after the socket dropped", false, undefined, null, "Kraft: reconnecting…"],
    ["connected in Restricted Mode", true, "Restricted Mode: trust this workspace", null, "Restricted Mode: trust this workspace"],
    ["connected and writable", true, undefined, null, undefined],
  ])("statusTooltip: %s", (_, connected, readOnly, err, want) => expect(statusTooltip(connected, readOnly, err)).toBe(want));

  it.each([
    ["the latest reply while connected", 3, 3, true, true],
    ["a reply a newer connect overtook", 2, 3, true, false],
    ["a reply after the store dropped", 3, 3, false, false],
  ])("acceptHealth: %s", (_, seq, current, connected, want) => expect(acceptHealth(seq, current, connected)).toBe(want));
});
