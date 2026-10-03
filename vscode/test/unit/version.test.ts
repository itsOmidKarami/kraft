import { describe, expect, it } from "vitest";
import { compatible, mismatch } from "../../src/core/version";

describe("compatible", () => {
  it.each([
    ["1.4.0", "1.4.2", true],
    ["1.4.0", "1.6.0", true],
    ["1.4.0", "1.3.9", false],
    ["1.4.0", "2.0.0", false],
    ["2.0.0", "1.4.0", false],
    ["2.0.0-rc.1", "2.0.0rc1", true],
    ["2.0.0", "1.5.0rc14", false],
    ["1.4.0", undefined, false],
    ["1.4.0", "garbage", false],
    ["1.4.0", "0.0.0+source", true],
    ["1.2.1", "0.1.dev1", true],
    ["0.0.0", "1.9.0", true],
    ["0.0.0", undefined, false],
  ])("extension %s, daemon %s → %s", (ext, daemon, want) => {
    expect(compatible(ext, daemon)).toBe(want);
  });
});

describe("mismatch", () => {
  it("is null whenever the two are compatible", () => {
    expect(mismatch("2.0.0", "2.1.0")).toBeNull();
    expect(mismatch("2.0.0", "0.0.0+source")).toBeNull();
  });

  it("names the Kraft release a server behind the extension needs", () => {
    expect(mismatch("2.0.0", "1.4.0")).toBe(
      "This extension (2.0.0) needs Kraft 2.0 or later; this server runs 1.4.0. Update Kraft. Actions are disabled until they match.",
    );
    expect(mismatch("2.3.1", "2.1.0")).toMatch(/^This extension \(2\.3\.1\) needs Kraft 2\.3 or later; this server runs 2\.1\.0\. Update Kraft\./);
  });

  it("tells a server on a newer major to update the extension, not Kraft", () => {
    const why = mismatch("2.0.0", "3.0.0");
    expect(why).toMatch(/runs 3\.0\.0, a newer major release than this extension \(2\.0\.0\) supports\. Update the extension\./);
    expect(why).not.toMatch(/Update Kraft/);
  });

  it("says when the version is missing or unreadable", () => {
    expect(mismatch("2.0.0", undefined)).toMatch(/did not report its version/);
    expect(mismatch("2.0.0", "garbage")).toMatch(/reports version garbage, which this extension \(2\.0\.0\) cannot read/);
  });
});
