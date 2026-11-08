import { describe, expect, it } from "vitest";
import { acceptHealth, downRow, every, onceEach, statusMenu, statusLabel, statusText, statusTooltip, statusWaiting } from "../../src/core/health";

describe("health", () => {
  it.each([
    [null, "Kraft isn't running — Start", "kraft.startDaemon"],
    [{ status: 0, detail: "x" }, "Kraft isn't running — Start", "kraft.startDaemon"],
    [{ status: 401, detail: "x" }, "Kraft refused the token — check kraft.url", "workbench.action.openSettings"],
    [{ status: 403, detail: "x" }, "Kraft refused the token — check kraft.url", "workbench.action.openSettings"],
    [{ status: 500, detail: "db locked" }, "Kraft error: db locked — Show log", "kraft.showLog"],
    [{ status: -1, detail: "SyntaxError" }, "Kraft error: SyntaxError — Show log", "kraft.showLog"],
  ])("downRow(%o)", (err, label, command) => expect(downRow(err)).toMatchObject({ label, command }));

  it.each([
    [{ status: 401, detail: "x" }, "Kraft: sign in again", "kraft.signIn"],
    [{ status: 0, detail: "x" }, "Kraft isn't running — Start", "kraft.startDaemon"],
  ])("downRow(%o) with a session", (err, label, command) => expect(downRow(err, true)).toMatchObject({ label, command }));

  it("a 403 with a session is the perimeter refusing, not an ended session", () => {
    const row = downRow({ status: 403, detail: "x" }, true);
    expect(row.command).not.toBe("kraft.signIn");
    expect(row.label).not.toBe("Kraft: sign in again");
  });

  it("opens settings at kraft.url", () => expect(downRow({ status: 401, detail: "" }).args).toEqual(["kraft.url"]));

  it("offers Start only when down", () => {
    expect(statusMenu(false).map((m) => m.label)).toEqual(["Show Log", "Open Web UI", "Start Kraft", "Sign in to a server…", "Retry Now"]);
    expect(statusMenu(true).map((m) => m.label)).toEqual(["Show Log", "Open Web UI", "Sign in to a server…", "Retry Now"]);
    expect(statusMenu(false).map((m) => m.choice)).toEqual(["log", "web", "start", "signIn", "retry"]);
  });

  it("offers sign-in, and no Start remotely", () => {
    expect(statusMenu(false, { remote: true }).map((m) => m.choice)).toEqual(["log", "web", "signIn", "retry"]);
  });

  it("the tooltip offers a session to a token on a remote url", () =>
    expect(statusTooltip(true, undefined, null, null, { signInOffered: true })).toContain("Sign in to use a session instead"));

  it("the tooltip says sign in again while down with a session", () =>
    expect(statusTooltip(false, undefined, { status: 401, detail: "x" }, null, { signedIn: true })).toBe("Kraft: sign in again"));

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
    ["writable with today's spend", true, undefined, null, "Today: $1.20 of $20.00", { spent_usd: 1.2, cap_usd: 20 }],
    ["read-only with today's spend", true, "why", null, "why\n\nToday: $0.500 (no daily cap)", { spent_usd: 0.5, cap_usd: null }],
    ["down ignores today's spend", false, undefined, null, "Kraft: reconnecting…", { spent_usd: 1.2, cap_usd: 20 }],
  ])("statusTooltip: %s", (_, connected, readOnly, err, want, today?) => expect(statusTooltip(connected, readOnly, err, today)).toBe(want));

  it.each([
    [true, false, 2, "$(bell-dot) Kraft 2"],
    [true, false, 0, "$(check) Kraft"],
    [true, true, 1, "$(bell-dot) Kraft (read-only) 1"],
    [false, false, 3, "$(debug-disconnect) Kraft"],
  ])("statusText(%s, %s, %s)", (connected, readOnly, needs, want) => expect(statusText(connected, readOnly, needs)).toBe(want));

  it.each([
    [true, 2, true, "Kraft: 2 items waiting on you"],
    [true, 1, true, "Kraft: 1 item waiting on you"],
    [true, 0, false, "Kraft: connected, nothing waiting"],
    [false, 3, false, "Kraft: not running"],
  ])("status bar, connected %s with %s waiting", (connected, needs, waiting, label) => {
    expect([statusWaiting(connected, needs), statusLabel(connected, needs)]).toEqual([waiting, label]);
  });

  it("every opens at most once per window, and always when forced", () => {
    let t = 0;
    const due = every(30_000, () => t);
    const seen = [due()];
    t = 29_999; seen.push(due());
    t = 30_000; seen.push(due());
    t = 30_001; seen.push(due(), due(true));
    t = 59_999; seen.push(due());
    t = 60_001; seen.push(due());
    expect(seen).toEqual([true, false, true, false, true, false, true]);
  });

  it.each([
    ["the latest reply while connected", 3, 3, true, true],
    ["a reply a newer connect overtook", 2, 3, true, false],
    ["a reply after the store dropped", 3, 3, false, false],
  ])("acceptHealth: %s", (_, seq, current, connected, want) => expect(acceptHealth(seq, current, connected)).toBe(want));
});
