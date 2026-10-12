import { describe, expect, it, vi } from "vitest";
import { ALREADY_STARTING, alreadyRunning, oneAtATime, startFailure, startInvocation } from "../../src/core/start";

const ARGS = ["admin", "start", "--detach"];

describe("startInvocation", () => {
  it.each([
    ["unset", undefined, "kraft"],
    ["blank", "  ", "kraft"],
    ["bare tilde", "~", "/home/u"],
    ["tilde slash", "~/bin/kraft", "/home/u/bin/kraft"],
    ["tilde backslash", "~\\bin\\kraft.exe", "/home/u\\bin\\kraft.exe"],
    ["trimmed", "  /usr/local/bin/kraft ", "/usr/local/bin/kraft"],
    ["spaces kept", "/opt/my tools/kraft", "/opt/my tools/kraft"],
    ["another user's home kept", "~bob/kraft", "~bob/kraft"],
  ])("%s", (_, setting, file) => expect(startInvocation(setting, "/home/u")).toEqual({ file, args: ARGS }));
});

describe("startFailure", () => {
  it.each([
    ["not found", { code: "ENOENT", message: "spawn kraft ENOENT" }, "Kraft couldn't find `kraft`. Install it, or set kraft.path to the kraft binary."],
    ["already running", { code: 1, stderr: "kraft: already running (pid 9) - kraft admin stop\n" }, "kraft: already running (pid 9) - kraft admin stop"],
    ["Kraft's first kraft: line", { code: 1, stderr: "kraft: detached start failed: RuntimeError: x\nTraceback\n  ...\nRuntimeError: x\nkraft: the whole log is /p\n" }, "kraft: detached start failed: RuntimeError: x"],
    ["no kraft: line, the last wins", { code: 2, stderr: "usage: kraft admin start [-h]\nkraft admin start: error: unrecognized arguments: --detach\n\n" }, "kraft admin start: error: unrecognized arguments: --detach"],
    ["timed out", { code: null, killed: true, stderr: "", message: "Command failed" }, "Kraft didn't start within 30 seconds."],
    ["no stderr", { code: "EACCES", message: "spawn /opt/kraft EACCES" }, "Kraft didn't start: spawn /opt/kraft EACCES"],
  ])("%s", (_, err, msg) => expect(startFailure(err, "kraft")).toBe(msg));

  it("names the file it could not find", () =>
    expect(startFailure({ code: "ENOENT" }, "/opt/my tools/kraft")).toContain("`/opt/my tools/kraft`"));
});

describe("alreadyRunning", () => {
  it.each([
    ["exit 1, already running", { code: 1, stderr: "kraft: already running (pid 9) - kraft admin stop\n" }, true],
    ["exit 1, a warning after it", { code: 1, stderr: "kraft: already running (pid 9) - kraft admin stop\nDeprecationWarning: x\n" }, true],
    ["exit 1, other error", { code: 1, stderr: "kraft: detached start failed: x\n" }, false],
    ["other exit", { code: 2, stderr: "kraft: already running (pid 9) - kraft admin stop\n" }, false],
    ["not found", { code: "ENOENT" }, false],
  ])("%s", (_, err, yes) => expect(alreadyRunning(err)).toBe(yes));
});

describe("oneAtATime", () => {
  it("a second click while one runs says Kraft is already starting, and spawns nothing", async () => {
    const say = vi.fn();
    const once = oneAtATime(() => say(ALREADY_STARTING));
    let finish!: () => void;
    const spawn = vi.fn(() => new Promise<void>((ok) => (finish = ok)));
    const first = once(spawn);
    expect(first).toBeDefined();
    expect(once(spawn)).toBeUndefined();
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(say).toHaveBeenCalledWith("Kraft is already starting.");
    finish();
    await first;
    expect(once(async () => "again")).toBeDefined();
    expect(say).toHaveBeenCalledTimes(1);
  });

  it("frees the gate when a start fails", async () => {
    const once = oneAtATime(() => {});
    await expect(once(() => { throw new Error("boom"); })).rejects.toThrow("boom");
    await expect(once(async () => 1)).resolves.toBe(1);
  });
});
