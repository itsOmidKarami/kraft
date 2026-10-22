import { describe, expect, it } from "vitest";
import { mergeView } from "./draft/fixture.merge";
import type { Authored, Result } from "./draft/types";
import { noRestart, rejectRestartTargets, unsetLabel } from "./fieldChoices";
import { fieldMeta } from "./fields";

const CHAINS = { area: "chains", key: "default" } as const;
const BEFORE_FINAL = ["spec", "implementation", "verification", "merge_request_feedback"];

/** The merge chain with its final review no longer final, in the file and as resolved. */
function noFinalGate(): Result {
  const r = mergeView().result;
  const plain = (nodes: unknown) => (nodes as Authored[]).map((n) => (n.id === "final_review" ? { ...n, chain_finalized: false } : n));
  const file = "chains/default.yaml";
  return {
    ...r,
    model: { [file]: { ...r.model[file], nodes: plain(r.model[file].nodes) } },
    resolved: { ...r.resolved!, chain: { ...r.resolved!.chain, nodes: plain(r.resolved!.chain.nodes) } },
  };
}

describe("on merge rejected's restart targets", () => {
  it.each([
    { id: "the exec nodes before the final gate the merge follows", r: mergeView().result, path: "merge", want: BEFORE_FINAL },
    { id: "with no final gate, every exec node before it", r: noFinalGate(), path: "merge", want: [...BEFORE_FINAL, "mark_ready"] },
    { id: "a final gate after the node limits nothing", r: mergeView().result, path: "merge_request_feedback", want: ["spec", "implementation", "verification"] },
    { id: "the first exec node has none", r: mergeView().result, path: "spec", want: [] },
    { id: "a node not in the chain has none", r: mergeView().result, path: "nope", want: [] },
  ])("$id", ({ r, path, want }) => {
    expect(rejectRestartTargets({ r, scope: CHAINS, path })).toEqual(want);
  });
});

describe("a restart row with nothing to pick", () => {
  const meta = fieldMeta("on_merge_rejected");
  it.each([
    { id: "has targets: it edits", scope: CHAINS, path: "merge", want: null },
    { id: "no exec node before the final gate", scope: CHAINS, path: "merge", r: mergeView({ tail: true }).result, want: "No earlier exec node to restart from." },
    { id: "a library node", scope: { area: "library", key: "library" } as const, path: "nodes.merge", want: "Read-only here: a library node has no chain to restart in." },
  ])("$id", ({ scope, path, r = mergeView().result, want }) => {
    expect(noRestart(meta, { r, scope, path })).toBe(want);
  });

  it("is only a merge rejection's: on base change restarts from the node itself", () => {
    expect(noRestart(fieldMeta("on_base_changed"), { r: mergeView().result, scope: CHAINS, path: "spec" })).toBeNull();
  });
});

describe("the restart picker's empty choice", () => {
  it.each([
    { id: "a merge rejection set: it drops the repair", field: "on_merge_rejected", current: { restart_from: "spec" }, want: "remove (drops the repair)" },
    { id: "no merge rejection yet", field: "on_merge_rejected", current: null, want: "not set" },
    { id: "on base change", field: "on_base_changed", current: { restart_from: "spec" }, want: "not set" },
  ])("$id", ({ field, current, want }) => {
    expect(unsetLabel(field, current)).toBe(want);
  });
});
