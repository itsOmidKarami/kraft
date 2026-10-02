import { describe, expect, it } from "vitest";
import type { ProbeCandidate } from "../../../types/settings";
import { chosenSource, missingLine, others, readFrom, setupLine, stopLine, testsCell, testsLine, testsTitle, withSource } from "./evidence";

const c = (over: Partial<ProbeCandidate>): ProbeCandidate => ({
  dir: "", role: "test", command: "just test", tier: "runner", source: "justfile recipe `test`", marker: "justfile",
  detector: "just", family: null, corroborated: false, chosen: true, ...over,
});

describe("probe evidence", () => {
  it("names a test's root sources, and every directory's setup sources", () => {
    const cands = [
      c({}),
      c({ role: "setup", command: "uv sync", source: "uv.lock", tier: "toolchain" }),
      c({ role: "setup", command: "npm ci", source: "package-lock.json", tier: "toolchain" }),
      c({ dir: "web", command: "npm test", source: "web/package.json script `test`" }),
      c({ dir: "web", role: "setup", command: "npm ci", source: "web/package-lock.json", tier: "toolchain" }),
      c({ command: "uv run pytest", source: "uv.lock", chosen: false }),
    ];
    expect(chosenSource(cands, "test")).toBe("justfile recipe `test`");
    // A five-part setup was labelled with the root's one source.
    expect(chosenSource(cands, "setup")).toBe("uv.lock + package-lock.json + web/: web/package-lock.json");
    expect(others(cands, "test")).toBe("uv run pytest (uv.lock)");
  });

  it("lists what it did not propose by role, leaving out what a saved command already runs", () => {
    const rest = ["a", "b", "c", "d"].map((x) => c({ command: `make ${x}`, source: "Makefile", chosen: false }));
    expect(others(rest, "test", [], 1)).toBe("make a (Makefile) and 3 more");
    const web = c({ dir: "web", command: "npm test", source: "web/package.json", chosen: false });
    expect(others([web], "test")).toBe("web/: npm test (web/package.json)");
    expect(others([web], "setup")).toBeNull();
    // jest's chosen `yarn test` was listed again, a picked setup's other half too.
    expect(others([web], "test", ["sh -c 'cd web && npm test'"])).toBeNull();
    const ci = c({ role: "setup", command: "npm ci", chosen: false });
    expect(others([ci], "setup", ["mix deps.get && npm ci"])).toBeNull();
    expect(others([ci], "setup", ["pnpm ci"])).toBe("npm ci (justfile recipe `test`)");
  });

  it("says a setup found but left undecided, rather than none found", () => {
    const bootstrap = c({ role: "setup", command: "make bootstrap" });
    expect(setupLine({ setup_command: null, candidates: [bootstrap], missing_setup: ["src"] }))
      .toBe("make bootstrap found, but src/ has nothing to prepare it");
    expect(setupLine({ setup_command: null, candidates: [], missing_setup: ["."] })).toBe("none found");
    // Each from its own directory: `npm ci && npm ci found` read like a command that fails at the root.
    const web = c({ dir: "web", role: "setup", command: "npm ci" });
    expect(setupLine({ setup_command: null, candidates: [bootstrap, web], missing_setup: ["."] }))
      .toBe("make bootstrap && (cd web && npm ci) found, but the root has nothing to prepare it");
    expect(setupLine({ setup_command: "", candidates: [] })).toBe("none needed");
  });

  it("says a monorepo has no tests at its root rather than heading it with its first scope", () => {
    const scopes = [{ paths: ["backend/**"], command: "sh -c 'cd backend && uv run pytest'" }, { paths: ["web/**"], command: "sh -c 'cd web && npm test'" }];
    const backend = c({ dir: "backend", role: "test", command: "uv run pytest" });
    expect(testsLine({ test_command: scopes[0].command, test_scopes: scopes, candidates: [backend] })).toBe("none at the root: each scope below has its own");
    expect(testsLine({ test_command: "make test", test_scopes: null, candidates: [c({ command: "make test" })] })).toBe("make test — from justfile recipe `test`");
    expect(testsLine({ test_command: null, stopped: [{ dir: ".", reason: "r", detector: "pyproject" }] })).toBe("none proposed");
  });

  it("says which programs a work item would fail on, by directory", () => {
    expect(missingLine([{ dir: "runtime-tests/deno", tool: "deno" }, { dir: ".", tool: "cargo" }]))
      .toBe("deno (runtime-tests/deno/), cargo (the root): not installed here, so a work item would fail on it");
    expect(missingLine([])).toBeNull();
  });

  it("lists a monorepo with its scopes, so its one command does not read as its whole suite", () => {
    const scopes = [{ paths: ["src/**"], command: "uv run pytest" }, { paths: ["web/**"], command: "npm test" }];
    expect(testsCell({ test_command: "uv run pytest", test_scopes: scopes })).toBe("uv run pytest + 2 scopes");
    expect(testsTitle({ test_command: "uv run pytest", test_scopes: scopes })).toBe("src/**: uv run pytest\nweb/**: npm test");
    expect(testsCell({ test_command: "make test" })).toBe("make test");
    expect(testsCell({})).toBe("—");
  });

  it("names the root as the root in a stop", () => {
    expect(stopLine({ dir: ".", reason: "a project", detector: "ruby" })).toBe("the root is a project");
    expect(stopLine({ dir: "api", reason: "a project", detector: "ruby" })).toBe("api/ is a project");
  });

  it("says which commit it read, so an edit not pushed there is not a surprise", () => {
    expect(readFrom("refs/remotes/origin/main")).toBe("origin/main, where work items start: commits not pushed there are not read");
    expect(readFrom("HEAD")).toMatch(/^this checkout's HEAD/);
    // null is a repo with no commit, which cannot be connected yet; no field says nothing.
    expect(readFrom(null)).toMatch(/the repo has no commit yet/);
    expect(readFrom(undefined)).toBeNull();
  });

  it("has nothing to say about a probe from a server that sends no candidates", () => {
    expect(chosenSource(undefined, "test")).toBeNull();
    expect(others(undefined, "test")).toBeNull();
    expect(withSource("pytest", null)).toBe("pytest");
    expect(withSource("pytest", "pytest.ini")).toBe("pytest — from pytest.ini");
  });
});
