import { describe, expect, it } from "vitest";
import type { SliceChild } from "../../types";
import { chainGraph, drawnOf } from "./graph";
import { groupSteps, tasksWords } from "./groupView";
import { sliced } from "./testkit";
import type { ItemDetail } from "./useItem";

const NOW = Date.parse("2026-11-09T15:00:00Z");
const SPLIT = { id: "slices", groundwork: [1, 2, 3], slices: [{ key: 1, title: "Client", tasks: [4, 5, 6] }, { key: 2, title: "Docs", tasks: [7] }], joined: 0 };
const kid = (key: number, display_status: string): SliceChild => ({ id: `c${key}`, title: `slice ${key}`, slice_key: key, status: "active", display_status, current_node_id: "verification", cost_usd: 0 });
const summary = (done: number, needs_you: number) => ({ done, needs_you, paused: 0, running: 0, queued: 0, dropped: 0, total: 2 });
/** A parent on `SLICED`, standing at `current`, its group in `phase`. */
const at = (current: string, phase: NonNullable<ItemDetail["group"]>["phase"], over: Partial<ItemDetail> = {}) => sliced(current, { group: { ...SPLIT, phase }, ...over });
const drawn = (item: ItemDetail) => chainGraph(item, [], NOW).nodes;
const view = (item: ItemDetail) => groupSteps(item, drawnOf(drawn(item), "slices")).map((st) => [st.id, st.tasks.map((t) => [t.label ?? t.id, t.state, t.meta])]);

const forked = { children: [kid(1, "done"), kid(2, "needs_you")], slices: summary(1, 1) };

describe("a slice group's view", () => {
  it.each([
    ["building its groundwork", at("slices.implementation", "groundwork", { display_status: "running" }), [
      ["implementation", [["implementation", "current", "tasks 1–3"]]],
      ["verification", [["verification", "todo", undefined]]],
      ["slices", [["slice 1", "todo", "tasks 4–6"], ["slice 2", "todo", "task 7"]]],
      ["join", [["join", "todo", undefined]]],
    ]],
    ["waiting for its slices", at("slices", "slices", { display_status: "waiting", ...forked }), [
      ["implementation", [["implementation", "done", "tasks 1–3"]]],
      ["verification", [["verification", "done", undefined]]],
      ["slices", [["slice 1", "done", "done"], ["slice 2", "amber", "needs you"]]],
      ["join", [["join", "todo", undefined]]],
    ]],
    // The item stays on the groundwork's last node while its slices run: that node is done all the same.
    ["waiting at the groundwork's last node", at("slices.verification", "slices", { display_status: "needs_you", ...forked }), [
      ["implementation", [["implementation", "done", "tasks 1–3"]]],
      ["verification", [["verification", "done", undefined]]],
      ["slices", [["slice 1", "done", "done"], ["slice 2", "amber", "needs you"]]],
      ["join", [["join", "todo", undefined]]],
    ]],
    ["joining them", at("slices", "joining", { display_status: "running", ...forked, group: { ...SPLIT, phase: "joining", joined: 1 } }), [
      ["implementation", [["implementation", "done", "tasks 1–3"]]],
      ["verification", [["verification", "done", undefined]]],
      ["slices", [["slice 1", "done", "done"], ["slice 2", "amber", "needs you"]]],
      ["join", [["join", "current", "slice 2 of 2"]]],
    ]],
    // With no groundwork the item runs none of the body itself; with no split it runs all of it, as a chain.
    ["with no groundwork", at("slices", "slices", { display_status: "waiting", ...forked, group: { ...SPLIT, groundwork: [], phase: "slices" } }), [
      ["slices", [["slice 1", "done", "done"], ["slice 2", "amber", "needs you"]]],
      ["join", [["join", "todo", undefined]]],
    ]],
    ["not split", at("slices.verification", "deciding", { display_status: "running", group: { ...SPLIT, groundwork: [], slices: [], phase: "deciding" } }), [
      ["implementation", [["implementation", "done", undefined]]],
      ["verification", [["verification", "current", undefined]]],
    ]],
  ])("draws the group %s", (_, item, want) => {
    expect(view(item)).toEqual(want);
  });

  it.each([
    ["building its groundwork", at("slices.implementation", "groundwork", { display_status: "running" }), { sub: "shared groundwork", subTone: "run" }],
    ["waiting, a slice needing you", at("slices", "slices", { display_status: "waiting", ...forked }), { meta: "1 of 2 slices done", sub: "slice 2 needs you", subTone: "amber" }],
    ["waiting", at("slices", "slices", { display_status: "waiting", children: [kid(1, "done"), kid(2, "running")], slices: summary(1, 0) }), { sub: "1 of 2 slices done", subTone: "muted" }],
    ["joining", at("slices", "joining", { display_status: "running", group: { ...SPLIT, phase: "joining", joined: 1 } }), { sub: "joining · slice 2 of 2", subTone: "run" }],
  ])("says on the chain what the group is doing while %s", (_, item, want) => {
    expect(drawn(item)[2]).toMatchObject(want);
  });

  it("says nothing more on a group the run has not reached", () => {
    const g = drawn(at("plan", "groundwork", { display_status: "running" }))[2];
    expect([g.sub, g.meta]).toEqual([undefined, undefined]);
  });

  it.each([[[4, 5, 6], "tasks 4–6"], [[2, 4], "tasks 2, 4"], [[1, 2], "tasks 1, 2"], [[7], "task 7"]])("writes %j as %s", (ns, want) => {
    expect(tasksWords(ns)).toBe(want);
  });
});
