import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { carryForwardNodeFields, parseChainReviewArtifact, withSteps } from "./chainReviewDiff";
import type { ChainNode } from "./types/work_item";

describe("carryForwardNodeFields", () => {
  it("fills an omitted field from the old node sharing its id", () => {
    const old: ChainNode[] = [
      { id: "verify", tasks: ["on.test.run"], gate_after: null, on_failure: ["on.repair"] },
    ];
    const revised = [{ id: "verify", tasks: ["on.test.run"], gate_after: null }];
    const out = carryForwardNodeFields(old, revised);
    expect(out[0].on_failure).toEqual(["on.repair"]);
  });

  it("leaves a field the reviewer set explicitly alone", () => {
    const old: ChainNode[] = [
      { id: "verify", tasks: [], gate_after: null, reject_to: "plan" },
    ];
    const revised = [{ id: "verify", tasks: [], gate_after: null, reject_to: "merge" }];
    const out = carryForwardNodeFields(old, revised);
    expect(out[0].reject_to).toBe("merge");
  });

  it("gives a brand-new node id null for every carryover field", () => {
    const out = carryForwardNodeFields([], [{ id: "extra", tasks: [], gate_after: null }]);
    expect(out[0].on_failure).toBeNull();
    expect(out[0].reject_to).toBeNull();
    expect(out[0].rebase_bounce_to).toBeNull();
  });

  it("strips an agent-authored auto_escalate before carrying the old value forward", () => {
    const old: ChainNode[] = [
      { id: "verify", tasks: [], gate_after: null, auto_escalate: true },
    ];
    const revised = [{ id: "verify", tasks: [], gate_after: null, auto_escalate: false }];
    const out = carryForwardNodeFields(old, revised);
    expect(out[0].auto_escalate).toBe(true);
  });
});

describe("parseChainReviewArtifact", () => {
  it("parses the JSON envelope after front matter", () => {
    const content = '---\nwork_item_ids: [x]\n---\n\n{"status":"ready_for_approval","rationale":"t"}\n';
    expect(parseChainReviewArtifact(content)).toEqual({
      status: "ready_for_approval",
      rationale: "t",
    });
  });

  it("returns null for non-JSON content", () => {
    expect(parseChainReviewArtifact("# just a markdown doc\n")).toBeNull();
  });

  it("returns null when revised_chain_nodes is not an array", () => {
    const content = '{"status":"ready_for_approval","revised_chain_nodes":{"verify":{}}}';
    expect(parseChainReviewArtifact(content)).toBeNull();
  });
});

describe("splice parity with the Python", () => {
  const fixture = JSON.parse(
    readFileSync(resolve(process.cwd(), "../tests/fixtures/chain_review_splice.json"), "utf8"),
  ) as {
    cases: {
      name: string;
      old_tail: ChainNode[];
      revised: Record<string, unknown>[];
      expected: Record<string, unknown>[];
    }[];
  };

  it.each(fixture.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const merged = carryForwardNodeFields(c.old_tail, c.revised).map(withSteps);
    expect(merged).toEqual(c.expected);
  });
});
