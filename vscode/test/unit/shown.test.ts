import { expect, it } from "vitest";
import { ShownRenders } from "../../src/core/shown";

it("records under the gate it is given", () => {
  const s = new ShownRenders();
  s.record("A", "revise", "d1");
  expect(s.get("A", "revise")).toEqual({ digest: "d1" });
  expect(s.get("A", "spec")).toBeUndefined();
});

it("remembers a render that had no digest", () => {
  const s = new ShownRenders();
  s.record("A", "spec", undefined);
  expect(s.get("A", "spec")).toEqual({ digest: undefined });
});

it("a new render replaces the remembered digest", () => {
  const s = new ShownRenders();
  s.record("A", "revise", "d1");
  s.record("A", "revise", "d2");
  expect(s.get("A", "revise")).toEqual({ digest: "d2" });
});

it("forget drops the item", () => {
  const s = new ShownRenders();
  s.record("A", "revise", "d1");
  s.forget("A");
  expect(s.get("A", "revise")).toBeUndefined();
});

it.each([
  { id: "gate moved", now: "review", kept: false },
  { id: "gate cleared", now: null, kept: false },
  { id: "same gate", now: "revise", kept: true },
  { id: "item not held by the store", now: undefined, kept: true },
])("reconcile: $id", ({ now, kept }) => {
  const s = new ShownRenders();
  s.record("A", "revise", "d1");
  s.reconcile(() => now);
  expect(s.get("A", "revise") !== undefined).toBe(kept);
});
