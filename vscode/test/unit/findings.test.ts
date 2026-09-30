import { describe, expect, it } from "vitest";
import detail from "./fixtures/detail-with-findings.json";
import { findingsByUri, findingsOf, marksBySeverity, showsFindings } from "../../src/core/findings";

describe("findingsOf", () => {
  const { located, unlocated } = findingsOf(detail as any);

  it("reads deferred findings and judge stop notes", () => {
    expect(located.map((f) => f.message).sort()).toEqual(["Off-by-one in add()", "Unused import"]);
    expect(unlocated.map((f) => f.message)).toEqual(["No test for the new branch"]);
  });

  it("maps severity and converts to 0-based lines", () => {
    const crit = located.find((f) => f.message.startsWith("Off-by-one"))!;
    expect(crit).toEqual({ file: "src/calc.py", line: 1, severity: "error", message: "Off-by-one in add()", source: "Kraft · review" });
    expect(located.find((f) => f.message === "Unused import")!.severity).toBe("information");
  });

  it("floors a line 0 finding at the first line", () => {
    const d = { deferred_findings: [{ severity: "minor", message: "m", file: "f", line: 0, source_plugin: "p" }] } as any;
    expect(findingsOf(d).located[0].line).toBe(0);
  });

  it("tolerates a detail with no findings fields", () => {
    expect(findingsOf({} as any)).toEqual({ located: [], unlocated: [] });
  });
});

it("shows findings only while a decision is pending", () => {
  expect(showsFindings({ pending_gate: "review" } as any)).toBe(true);
  expect(showsFindings({ status: "paused" } as any)).toBe(true);
  expect(showsFindings({ status: "active" } as any)).toBe(false);
});

it("puts each finding on the diff's right side only, so Problems lists it once", () => {
  const d = { deferred_findings: [{ severity: "minor", message: "a", file: "src/x.py", line: 2, source_plugin: "p" }, { severity: "minor", message: "b", file: "src/x.py", line: 5, source_plugin: "p" }] } as any;
  const byUri = findingsByUri("K-1", d);
  expect([...byUri.keys()]).toEqual(["kraft-wt:/K-1/src/x.py"]);
  expect(byUri.get("kraft-wt:/K-1/src/x.py")!.map((f) => f.message)).toEqual(["a", "b"]);
});

it("marks every finding on the diff by its severity, minor ones included", () => {
  const d = { deferred_findings: [{ severity: "minor", message: "a", file: "x.py", line: 2, source_plugin: "p" }, { severity: "critical", message: "b", file: "x.py", line: 5, source_plugin: "p" }] } as any;
  const marks = marksBySeverity(findingsByUri("K-1", d).get("kraft-wt:/K-1/x.py"));
  expect(marks.information.map((f) => f.message)).toEqual(["a"]);
  expect(marks.error.map((f) => f.message)).toEqual(["b"]);
  expect(marks.warning).toEqual([]);
});
