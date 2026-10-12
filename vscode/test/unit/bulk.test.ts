import { describe, expect, it } from "vitest";
import { bulkPlan, bulkSummary, selectedItems } from "../../src/core/bulk";
import { item, stopped } from "./fixtures/item";

const a = (id: string, actions: string[]) => item({ id, actions: actions as never });

describe("bulkPlan", () => {
  it.each([
    ["takes the items whose door is open", "pause", [a("a", ["pause"]), a("b", ["resume"])], { ids: ["a"], skipped: [{ id: "b", why: "can't pause now" }] }],
    ["skips one from a server that sends no doors", "archive", [item({ id: "c" })], { ids: [], skipped: [{ id: "c", why: "can't archive now" }] }],
    ["resume skips a question stop: it needs an answer of its own", "resume",
      [a("p", ["resume"]), stopped("question", { id: "q", actions: ["resume", "retry"] })],
      { ids: ["p"], skipped: [{ id: "q", why: "needs an answer" }] }],
    ["keeps the selection's order", "restore", [a("z", ["restore"]), a("y", ["restore"])], { ids: ["z", "y"], skipped: [] }],
  ] as const)("%s", (_, verb, items, want) => expect(bulkPlan(verb, [...items])).toEqual(want));
});

describe("bulkSummary", () => {
  it.each([
    ["all done", [{ id: "a", ok: true }, { id: "b", ok: true }], [], "Paused 2."],
    ["one refused", [{ id: "a", ok: true }, { id: "b", ok: false, error: "work item is being started…" }], [], "Paused 1. Not paused: b (work item is being started…)."],
    ["one skipped", [{ id: "a", ok: true }], [{ id: "c", why: "can't pause now" }], "Paused 1. Not paused: c (can't pause now)."],
    ["refusals before skips", [{ id: "b", ok: false, error: "no" }], [{ id: "c", why: "can't pause now" }], "Not paused: b (no), c (can't pause now)."],
  ])("%s", (_, results, skipped, want) => expect(bulkSummary("pause", results, skipped)).toBe(want));

  it.each([
    ["resume", "Resumed 1."],
    ["archive", "Archived 1."],
    ["restore", "Restored 1."],
  ] as const)("says %s in the past tense", (verb, want) => expect(bulkSummary(verb, [{ id: "a", ok: true }], [])).toBe(want));
});

describe("selectedItems", () => {
  const [x, y] = [item({ id: "x" }), item({ id: "y" })];
  const row = (i: typeof x) => ({ kind: "item", item: i });
  it.each([
    ["the selection, when the click is inside it", row(x), [row(x), row(y)], ["x", "y"]],
    ["the clicked row alone, when it is outside the selection", row(x), [row(y)], ["x"]],
    ["the clicked row alone, with no selection passed", row(x), undefined, ["x"]],
    ["only the item rows of the selection", row(x), [row(x), { kind: "group" }, { kind: "findings", item: y }], ["x"]],
    ["each item once", row(x), [row(x), row(y), row(y)], ["x", "y"]],
    ["nothing from the palette", undefined, undefined, []],
    ["nothing for a findings row", { kind: "findings", item: x }, [row(x), row(y)], []],
  ])("acts on %s", (_, node, nodes, want) => expect(selectedItems(node, nodes).map((i) => i.id)).toEqual(want));
});
