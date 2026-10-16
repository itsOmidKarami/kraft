import { afterEach, describe, expect, it, vi } from "vitest";
import { withBase, withoutBase } from "./base";

afterEach(() => vi.unstubAllEnvs());

describe("the app's base path", () => {
  it.each([
    ["/", "/work-items/abc", "/work-items/abc"],
    ["/next/demo/", "/work-items/abc", "/next/demo/work-items/abc"],
    ["/next/demo/", "/icon.svg", "/next/demo/icon.svg"],
  ])("under %s, withBase(%s) is %s", (base, path, out) => {
    vi.stubEnv("BASE_URL", base);
    expect(withBase(path)).toBe(out);
  });

  it.each([
    ["/", "/settings/policy", "/settings/policy"],
    ["/next/demo/", "/next/demo/settings/policy", "/settings/policy"],
    ["/next/demo/", "/next/demo", "/"],
    ["/next/demo/", "/next/demo/", "/"],
    // The docs' own pages beside the demo, and an address that only starts with the base's letters.
    ["/next/demo/", "/next/guides/x", null],
    ["/next/demo/", "/next/demonstration", null],
  ])("under %s, withoutBase(%s) is %s", (base, pathname, out) => {
    vi.stubEnv("BASE_URL", base);
    expect(withoutBase(pathname)).toBe(out);
  });
});
