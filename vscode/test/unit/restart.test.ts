import { describe, expect, it } from "vitest";
import type { FixTarget } from "../../src/core/api";
import { nodeToSend, restartChoices, type RestartChoice } from "../../src/core/restart";

const N = (...ids: string[]) => ids.map((id) => ({ id, kind: id.startsWith("gate:") ? "gate" : "exec" }));
const fix = (over: Partial<FixTarget> = {}): FixTarget => ({ gate: "review", node: "impl", then: [], round: null, ...over });
const ids = (r: ReturnType<typeof restartChoices>) => ("refused" in r ? r : r.map((c) => c.node));

describe("restartChoices", () => {
  it.each<{ id: string; nodes: { id: string; kind?: string }[]; at: { gate: string } | { current: string }; fix: FixTarget; want: string[] | { refused: string } }>([
    { id: "gate, default first", nodes: N("spec", "plan", "impl", "review"), at: { gate: "review" }, fix: fix(), want: ["impl", "spec", "plan"] },
    {
      id: "gateless, at or before current", nodes: N("spec", "impl", "test", "ship"), at: { current: "test" },
      fix: fix({ gate: null, then: ["test"], reason: "threads on a.py" }), want: ["impl", "spec", "test"],
    },
    { id: "never a gate node", nodes: N("spec", "gate:spec", "impl", "review"), at: { gate: "review" }, fix: fix(), want: ["impl", "spec"] },
    { id: "gateless, never a gate node", nodes: N("spec", "gate:spec", "impl"), at: { current: "impl" }, fix: fix({ gate: null }), want: ["impl", "spec"] },
    { id: "out of rounds", nodes: N("impl", "review"), at: { gate: "review" }, fix: fix({ round: { n: 4, max: 3 } }), want: { refused: "No fix rounds left." } },
    { id: "last round left", nodes: N("impl", "review"), at: { gate: "review" }, fix: fix({ round: { n: 3, max: 3 } }), want: ["impl"] },
    { id: "a default the view does not list is still first", nodes: N("spec", "review"), at: { gate: "review" }, fix: fix(), want: ["impl", "spec"] },
  ])("$id", ({ nodes, at, fix: f, want }) => expect(ids(restartChoices(nodes, at, f))).toEqual(want));

  it("the default carries the web UI's sentence at a gate", () => {
    const [first] = restartChoices(N("spec", "impl", "test", "review"), { gate: "review" }, fix({ then: ["test"] })) as RestartChoice[];
    expect(first).toEqual({ node: "impl", isDefault: true, label: "impl (default)", description: "then test → review" });
  });

  it("and says why with none", () => {
    const [first] = restartChoices(N("impl", "test"), { current: "test" }, fix({ gate: null, reason: "threads on a.py" })) as RestartChoice[];
    expect(first.description).toBe("why: threads on a.py");
  });
});

it("nodeToSend names only a node other than the default", () => {
  expect(nodeToSend({ node: "impl", isDefault: true, label: "impl (default)" })).toBeUndefined();
  expect(nodeToSend({ node: "spec", isDefault: false, label: "spec" })).toBe("spec");
});
