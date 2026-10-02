import { describe, expect, it } from "vitest";
import type { ProbeCandidate } from "../../../types/settings";
import { chosenSource, others, readFrom, withSource } from "./evidence";

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
    expect(others(cands)).toBe("uv run pytest (uv.lock)");
  });

  it("names the first few commands it did not propose, with their directory, and counts the rest", () => {
    const rest = ["a", "b", "c", "d"].map((x) => c({ command: `make ${x}`, source: "Makefile", chosen: false }));
    expect(others([...rest, c({ dir: "web", command: "npm test", source: "web/package.json", chosen: false })], 1))
      .toBe("make a (Makefile) and 4 more");
    expect(others([c({ dir: "web", command: "npm test", source: "web/package.json", chosen: false })]))
      .toBe("web/: npm test (web/package.json)");
  });

  it("says which commit it read, so an edit not pushed there is not a surprise", () => {
    expect(readFrom("refs/remotes/origin/main")).toBe("origin/main, where work items start: commits not pushed there are not read");
    expect(readFrom("HEAD")).toMatch(/^this checkout's HEAD/);
    expect(readFrom(null)).toBeNull();
  });

  it("has nothing to say about a probe from a server that sends no candidates", () => {
    expect(chosenSource(undefined, "test")).toBeNull();
    expect(others(undefined)).toBeNull();
    expect(withSource("pytest", null)).toBe("pytest");
    expect(withSource("pytest", "pytest.ini")).toBe("pytest — from pytest.ini");
  });
});
