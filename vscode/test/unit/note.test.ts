import { describe, expect, it } from "vitest";
import { composeNote, destinationOf } from "../../src/core/note";

describe("composeNote", () => {
  it("lists comments by file and line, then the summary", () => {
    expect(
      composeNote({
        comments: [
          { file: "src/b.py", line: 9, body: "rename this" },
          { file: "src/a.py", line: 42, body: "off by one\nsee the test" },
        ],
        summary: "Close, two fixes.",
      }),
    ).toBe(
      "Review comments:\n" +
        "- src/a.py:42 — off by one\n  see the test\n" +
        "- src/b.py:9 — rename this\n" +
        "- (general) — Close, two fixes.",
    );
  });

  it("omits an empty summary", () => {
    expect(composeNote({ comments: [{ file: "f", line: 1, body: "x" }], summary: "  " })).toBe("Review comments:\n- f:1 — x");
  });
});

it("marks a base-side comment", () => {
  expect(composeNote({ comments: [{ file: "f", line: 3, body: "x", side: "base" }] })).toBe("Review comments:\n- f:3 (base) — x");
});

describe("destinationOf", () => {
  it("rejects at a pending gate", () => {
    expect(destinationOf({ pending_gate: "review", status: "needs_human" } as any)).toEqual({ kind: "reject", gate: "review" });
  });
  it("resumes a steerable paused item", () => {
    expect(destinationOf({ status: "paused", steerable: true } as any)).toEqual({ kind: "resume" });
  });
  it("refuses otherwise, saying why", () => {
    expect(destinationOf({ status: "paused", steerable: false } as any)).toMatchObject({ kind: "none" });
    expect(destinationOf({ status: "active" } as any)).toMatchObject({ kind: "none" });
    expect(destinationOf(undefined)).toMatchObject({ kind: "none" });
  });
});
