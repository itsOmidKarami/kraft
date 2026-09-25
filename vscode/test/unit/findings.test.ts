import { describe, expect, it } from "vitest";
import detail from "./fixtures/detail-with-findings.json";
import { findingsOf, showsFindings } from "../../src/core/findings";

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
