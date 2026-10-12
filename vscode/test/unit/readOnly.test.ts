import { describe, expect, it, vi } from "vitest";
import { RESTRICTED, VERSION_REFUSAL, refusal, tooltip, whenTrusted } from "../../src/core/readOnly";

describe("refusal", () => {
  it.each([
    { id: "trusted, versions match", s: { trusted: true, version: null }, want: null },
    { id: "untrusted", s: { trusted: false, version: null }, want: RESTRICTED },
    { id: "trusted, mismatch", s: { trusted: true, version: "v" }, want: VERSION_REFUSAL },
    { id: "untrusted and mismatch", s: { trusted: false, version: "v" }, want: RESTRICTED },
  ])("$id", ({ s, want }) => expect(refusal(s)).toBe(want));
  it("is exactly the spec's copy", () =>
    expect(RESTRICTED).toBe("Kraft is read-only in Restricted Mode: trust this workspace to act."));
});

describe("tooltip", () => {
  it("names both reasons", () => {
    const t = tooltip({ trusted: false, version: "Update Kraft." })!;
    expect(t).toMatch(/Restricted Mode/);
    expect(t).toMatch(/Update Kraft\./);
  });
  it("is the version reason alone when trusted", () =>
    expect(tooltip({ trusted: true, version: "Update Kraft." })).toBe("Update Kraft."));
  it("is undefined when nothing is wrong", () =>
    expect(tooltip({ trusted: true, version: null })).toBeUndefined());
});

describe("whenTrusted", () => {
  const fake = () => {
    const sub = { dispose: vi.fn() };
    let listener: (() => void) | undefined;
    const onGrant = vi.fn((l: () => void) => {
      listener = l;
      return sub;
    });
    return { sub, onGrant, register: vi.fn(), fire: () => listener!() };
  };

  it("registers at once when trusted and does not subscribe", () => {
    const f = fake();
    whenTrusted(true, f.onGrant, f.register);
    expect(f.register).toHaveBeenCalledTimes(1);
    expect(f.onGrant).not.toHaveBeenCalled();
  });
  it("waits for the grant when untrusted", () => {
    const f = fake();
    whenTrusted(false, f.onGrant, f.register);
    expect(f.onGrant).toHaveBeenCalledTimes(1);
    expect(f.register).not.toHaveBeenCalled();
    f.fire();
    expect(f.register).toHaveBeenCalledTimes(1);
  });
  it("registers once however often the grant fires", () => {
    const f = fake();
    whenTrusted(false, f.onGrant, f.register);
    f.fire();
    expect(f.sub.dispose).toHaveBeenCalled();
    f.fire();
    expect(f.register).toHaveBeenCalledTimes(1);
  });
  it.each([
    { id: "trusted: nothing to dispose", trusted: true, want: (_: unknown) => undefined },
    { id: "untrusted: the subscription", trusted: false, want: (sub: unknown) => sub },
  ])("returns $id", ({ trusted, want }) => {
    const f = fake();
    expect(whenTrusted(trusted, f.onGrant, f.register)).toBe(want(f.sub));
  });
});
