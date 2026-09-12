import { afterEach, describe, expect, it, vi } from "vitest";

// Kraft-m1e8: a missing fixture server must fail the run, not silently
// redirect Playwright at whatever answers on 127.0.0.1:8765 — in practice the
// installed daemon, where settings.spec connects repos and edits policy
// against a human's real ~/.kraft.
describe("playwright.config's KRAFT_E2E_BASE", () => {
  const original = process.env.KRAFT_E2E_BASE;

  afterEach(() => {
    if (original === undefined) delete process.env.KRAFT_E2E_BASE;
    else process.env.KRAFT_E2E_BASE = original;
    vi.resetModules();
  });

  it("throws when unset", async () => {
    delete process.env.KRAFT_E2E_BASE;
    vi.resetModules();
    await expect(import("./playwright.config")).rejects.toThrow(/KRAFT_E2E_BASE/);
  });

  it("uses it as baseURL when set", async () => {
    process.env.KRAFT_E2E_BASE = "http://127.0.0.1:59999";
    vi.resetModules();
    const mod = await import("./playwright.config");
    expect(mod.default.use?.baseURL).toBe("http://127.0.0.1:59999");
  });
});
