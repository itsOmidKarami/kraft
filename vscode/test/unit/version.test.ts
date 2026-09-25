import { describe, expect, it } from "vitest";
import { compatible } from "../../src/core/version";

describe("compatible", () => {
  it.each([
    ["1.4.0", "1.4.2", true],
    ["1.4.0", "1.6.0", true],
    ["1.4.0", "1.3.9", false],
    ["1.4.0", "2.0.0", false],
    ["1.4.0", undefined, false],
    ["1.4.0", "garbage", false],
    ["1.4.0", "0.0.0+source", true],
    ["0.0.0", "1.9.0", true],
    ["0.0.0", undefined, false],
  ])("extension %s, daemon %s → %s", (ext, daemon, want) => {
    expect(compatible(ext, daemon)).toBe(want);
  });
});
