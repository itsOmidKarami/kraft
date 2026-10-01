import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../../types";
import { appliedAt, appliedOverrides } from "./applied";

const ev = (seq: number, payload: Record<string, unknown>, type = "chain_revised"): KraftEvent => ({ seq, work_item_id: "w1", type, payload, created_at: "2026-10-01T00:00:00Z" });
const draft = (...changes: unknown[]) => ev(1, { source: "draft", gate: null, changes, diff: [] });

describe("appliedOverrides", () => {
  it("folds draft overrides per path and field, a later event winning", () => {
    const got = appliedOverrides([
      draft({ op: "override", path: "verify.main.run", task_config: { model: "a", effort: "low" } }),
      { ...draft({ op: "override", path: "verify.main.run", task_config: { model: "b" }, policy: { time_cap_minutes: 9 } }), seq: 2 },
    ]);
    expect(got).toEqual({ "verify.main.run": { task_config: { model: "b", effort: "low" }, policy: { time_cap_minutes: 9 } } });
  });
  it("ignores a revision that is not from a draft, other ops, and other event types", () => {
    expect(appliedOverrides([
      ev(1, { source: "gate", changes: [{ op: "override", path: "a", policy: { x: 1 } }] }),
      draft({ op: "add_node", after: "a", node: { id: "b" } }, { op: "skip", path: "a.b.c" }),
      ev(3, { source: "draft", changes: [{ op: "override", path: "a", policy: { x: 1 } }] }, "node_started"),
    ])).toEqual({});
  });
  it("scopes a lookup to a path and what is under it", () => {
    const all = { "verify.main.run": { policy: { a: 1 } }, "verify": { policy: { b: 1 } }, "ship": { policy: { c: 1 } } };
    expect(appliedAt(all, "verify").map(([p]) => p)).toEqual(["verify.main.run", "verify"]);
    expect(appliedAt(all, "verify.main.run").map(([p]) => p)).toEqual(["verify.main.run"]);
  });
});
