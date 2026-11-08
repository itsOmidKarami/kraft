import { describe, expect, it, vi } from "vitest";
import type { SignInResult } from "../../src/core/session";
import { runSignIn, type SignInDeps } from "../../src/core/signInFlow";

const deps = (over: Partial<SignInDeps> & { url?: string; password?: string | undefined; result?: SignInResult } = {}) => {
  const { url = "https://k.example", result = { ok: true, session: "SESS" } as SignInResult, ...rest } = over;
  const password = "password" in over ? over.password : "hunter2";
  const d = {
    prefill: "",
    askUrl: vi.fn(async () => url as string | undefined),
    askPassword: vi.fn(async () => password),
    confirmPlainHttp: vi.fn(async () => true),
    signIn: vi.fn(async () => result),
    store: { set: vi.fn(async () => {}) },
    writeUrl: vi.fn(async () => {}),
    ...rest,
  } as unknown as SignInDeps & { askUrl: any; askPassword: any; confirmPlainHttp: any; signIn: any; store: { set: any }; writeUrl: any };
  return d;
};

describe("runSignIn", () => {
  it("signs in to https: stores the session under the normalized URL, then writes kraft.url", async () => {
    const d = deps({ url: "https://k.example" });
    const out = await runSignIn(d);
    expect(out).toEqual({ kind: "signed-in", url: "https://k.example:443" });
    expect(d.signIn).toHaveBeenCalledWith("https://k.example:443", "hunter2");
    expect(d.store.set).toHaveBeenCalledWith("https://k.example:443", "SESS");
    expect(d.writeUrl).toHaveBeenCalledWith("https://k.example:443");
    expect(d.confirmPlainHttp).not.toHaveBeenCalled();
  });

  it("passes the current kraft.url to the address box", async () => {
    const d = deps({ prefill: "http://old:1" });
    await runSignIn(d);
    expect(d.askUrl).toHaveBeenCalledWith("http://old:1");
  });

  it("warns once before sending a password over http to another machine", async () => {
    const d = deps({ url: "http://192.168.1.5:8765", confirmPlainHttp: vi.fn(async () => false) });
    expect(await runSignIn(d)).toEqual({ kind: "cancelled" });
    expect(d.confirmPlainHttp).toHaveBeenCalledTimes(1);
    expect(d.confirmPlainHttp).toHaveBeenCalledWith("192.168.1.5:8765");
    expect(d.askPassword).not.toHaveBeenCalled();
    expect(d.signIn).not.toHaveBeenCalled();
    expect(d.store.set).not.toHaveBeenCalled();
    expect(d.writeUrl).not.toHaveBeenCalled();
  });

  it("goes on when the warning is accepted", async () => {
    const d = deps({ url: "http://192.168.1.5:8765" });
    expect((await runSignIn(d)).kind).toBe("signed-in");
  });

  it("does not warn for http to this machine", async () => {
    const d = deps({ url: "http://localhost:9000" });
    await runSignIn(d);
    expect(d.confirmPlainHttp).not.toHaveBeenCalled();
  });

  it("a wrong password stores nothing and writes nothing", async () => {
    const d = deps({ result: { ok: false, kind: "wrong-password", message: "Wrong password." } });
    expect(await runSignIn(d)).toEqual({ kind: "refused", message: "Wrong password." });
    expect(d.store.set).not.toHaveBeenCalled();
    expect(d.writeUrl).not.toHaveBeenCalled();
  });

  it("an address with a path is refused before the password is asked", async () => {
    const d = deps({ url: "https://proxy/kraft/" });
    const out = await runSignIn(d);
    expect(out).toMatchObject({ kind: "refused", message: expect.stringMatching(/path/) });
    expect(d.askPassword).not.toHaveBeenCalled();
  });

  it("an empty password is refused without a request", async () => {
    const d = deps({ password: "" });
    expect(await runSignIn(d)).toEqual({ kind: "refused", message: "Enter the password." });
    expect(d.signIn).not.toHaveBeenCalled();
  });

  it.each([
    ["the address box", { askUrl: vi.fn(async () => undefined) }],
    ["the password box", { password: undefined }],
  ])("cancelling at %s stores nothing", async (_id, over) => {
    const d = deps(over as any);
    expect(await runSignIn(d)).toEqual({ kind: "cancelled" });
    expect(d.signIn).not.toHaveBeenCalled();
    expect(d.store.set).not.toHaveBeenCalled();
    expect(d.writeUrl).not.toHaveBeenCalled();
  });

  it("a store that fails leaves kraft.url alone", async () => {
    const d = deps({ store: { set: vi.fn(async () => { throw new Error("no keychain"); }) } as any });
    await expect(runSignIn(d)).rejects.toThrow("no keychain");
    expect(d.writeUrl).not.toHaveBeenCalled();
  });

  it.each([
    ["signed in", {}],
    ["refused", { result: { ok: false, kind: "other", message: "boom" } as SignInResult }],
    ["a bad address", { url: "ftp://x" }],
  ])("the password is never in the outcome: %s", async (_id, over) => {
    const out = await runSignIn(deps(over as any));
    expect(JSON.stringify(out)).not.toContain("hunter2");
  });
});
