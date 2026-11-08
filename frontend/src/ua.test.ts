// @vitest-environment node
import { describe, expect, it } from "vitest";
import { parseUserAgent } from "./ua";

describe("parseUserAgent", () => {
  it("reads a Mac + Safari UA", () => {
    expect(
      parseUserAgent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
      ),
    ).toBe("Mac · Safari");
  });
  it("reads an iPhone UA", () => {
    expect(
      parseUserAgent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile Safari/605.1.15",
      ),
    ).toBe("iPhone · Safari");
  });
  it("falls back on nothing", () => {
    expect(parseUserAgent(null)).toBe("unknown device");
  });
  it("shows a Kraft client's own label as given and caps it at 60 characters", () => {
    expect(parseUserAgent("Kraft console on work-laptop")).toBe("Kraft console on work-laptop");
    const long = `Kraft console on ${"h".repeat(80)}`;
    expect(parseUserAgent(long)).toBe(long.slice(0, 60));
  });
});
