import { describe, expect, it } from "vitest";
import type { ProbeCandidate } from "../../../types/settings";
import { chosenSource, otherCount, withSource } from "./evidence";

const c = (over: Partial<ProbeCandidate>): ProbeCandidate => ({
  dir: "", role: "test", command: "just test", tier: "runner", source: "justfile recipe `test`", marker: "justfile",
  detector: "just", family: null, corroborated: false, chosen: true, ...over,
});

describe("probe evidence", () => {
  it("names the root's chosen sources, joining two toolchains' setups", () => {
    const cands = [
      c({}),
      c({ role: "setup", command: "uv sync", source: "uv.lock", tier: "toolchain" }),
      c({ role: "setup", command: "npm ci", source: "package-lock.json", tier: "toolchain" }),
      c({ dir: "web", command: "npm test", source: "web/package.json script `test`" }),
      c({ command: "uv run pytest", source: "uv.lock", chosen: false }),
    ];
    expect(chosenSource(cands, "test")).toBe("justfile recipe `test`");
    expect(chosenSource(cands, "setup")).toBe("uv.lock + package-lock.json");
    expect(otherCount(cands)).toBe(1);
  });

  it("has nothing to say about a probe from a server that sends no candidates", () => {
    expect(chosenSource(undefined, "test")).toBeNull();
    expect(otherCount(undefined)).toBe(0);
    expect(withSource("pytest", null)).toBe("pytest");
    expect(withSource("pytest", "pytest.ini")).toBe("pytest — from pytest.ini");
  });
});
