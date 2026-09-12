import { afterEach, describe, expect, it, vi } from "vitest";

describe("scaledTimeout", () => {
  const original = process.env.KRAFT_E2E_TIMEOUT_SCALE;

  afterEach(() => {
    if (original === undefined) delete process.env.KRAFT_E2E_TIMEOUT_SCALE;
    else process.env.KRAFT_E2E_TIMEOUT_SCALE = original;
    vi.resetModules();
  });

  it("defaults to today's value when the env var is unset", async () => {
    delete process.env.KRAFT_E2E_TIMEOUT_SCALE;
    vi.resetModules();
    const { scaledTimeout } = await import("./e2e-timing");
    expect(scaledTimeout(15_000)).toBe(15_000);
  });

  it("multiplies the base budget by the env var when set", async () => {
    process.env.KRAFT_E2E_TIMEOUT_SCALE = "3";
    vi.resetModules();
    const { scaledTimeout } = await import("./e2e-timing");
    expect(scaledTimeout(15_000)).toBe(45_000);
  });
});
