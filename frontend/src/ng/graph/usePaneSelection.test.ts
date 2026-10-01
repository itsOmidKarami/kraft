import { describe, expect, it } from "vitest";
import { initialPane, paneReducer, type PaneAction, type PaneState, type Sel } from "./usePaneSelection";

const run = (s: PaneState, ...as: PaneAction[]) => as.reduce(paneReducer, s);
const node = (n: string): Sel => ({ kind: "node", node: n });
const task: Sel = { kind: "task", node: "v", step: "checks", task: "lint" };
const chain: Sel = { kind: "chain" };

describe("pane selection (Decisions §9)", () => {
  it("starts on the chain, open or not as the page says", () => {
    expect(initialPane(true)).toEqual({ level: "chain", sel: chain, open: true, userCollapsed: false });
    expect(initialPane(false).open).toBe(false);
  });
  it("opens the pane when something is picked after the floor", () => {
    const s = run(initialPane(true), { type: "background" }, { type: "pick", sel: node("a") });
    expect(s).toMatchObject({ sel: node("a"), open: true });
  });
  it("drops an empty-canvas click to the floor's rail without counting as a collapse", () => {
    const s = run(initialPane(true), { type: "pick", sel: node("a") }, { type: "background" });
    expect(s).toMatchObject({ sel: chain, open: false, userCollapsed: false });
  });
  it("keeps a collapsed pane collapsed while the person moves between items", () => {
    const s = run(initialPane(true), { type: "pick", sel: node("a") }, { type: "collapse" }, { type: "pick", sel: node("b") });
    expect(s).toMatchObject({ sel: node("b"), open: false, userCollapsed: true });
  });
  it("keeps an open pane open on another pick", () => {
    expect(run(initialPane(true), { type: "pick", sel: node("a") }, { type: "pick", sel: node("b") }).open).toBe(true);
  });
  it("expands when the selected item is picked again while collapsed", () => {
    const s = run(initialPane(true), { type: "pick", sel: node("a") }, { type: "collapse" }, { type: "pick", sel: node("a") });
    expect(s).toMatchObject({ open: true, userCollapsed: false });
    // ...and a pick after that opens again.
    expect(run(s, { type: "pick", sel: node("b") }).open).toBe(true);
  });
  it("leaves an open pane alone when its item is picked again", () => {
    const s = run(initialPane(true), { type: "pick", sel: node("a") });
    expect(run(s, { type: "pick", sel: node("a") })).toBe(s);
  });
  it("expands on the rail or a double-click, optionally selecting", () => {
    const s = run(initialPane(true), { type: "collapse" }, { type: "expand", sel: task });
    expect(s).toMatchObject({ sel: task, open: true, userCollapsed: false });
    expect(run(initialPane(false), { type: "expand" })).toMatchObject({ sel: chain, open: true });
  });
  it("keeps the selection when collapsing", () => {
    expect(run(initialPane(true), { type: "pick", sel: node("a") }, { type: "collapse" }).sel).toEqual(node("a"));
  });
  it("enters a node view keeping the pane as it was, on the node or what was picked", () => {
    const open = run(initialPane(true), { type: "pick", sel: node("v") }, { type: "focus", node: "v" });
    expect(open).toMatchObject({ level: "node", node: "v", sel: node("v"), open: true });
    const shut = run(initialPane(true), { type: "collapse" }, { type: "focus", node: "v", sel: task });
    expect(shut).toMatchObject({ level: "node", sel: task, open: false });
  });
  it("floors to the node inside a node view, and falls back there when its item is removed", () => {
    const inNode = run(initialPane(true), { type: "focus", node: "v", sel: task });
    expect(run(inNode, { type: "background" })).toMatchObject({ sel: node("v"), open: false });
    expect(run(inNode, { type: "removed" })).toMatchObject({ sel: node("v"), open: false });
  });
  it("resets to the chain's rail on going back", () => {
    const s = run(initialPane(true), { type: "focus", node: "v", sel: task }, { type: "back" });
    expect(s).toMatchObject({ level: "chain", node: undefined, sel: chain, open: false });
  });
  it("escapes: collapse first, then back to the chain, then nothing", () => {
    const inNode = run(initialPane(true), { type: "focus", node: "v", sel: task });
    const once = run(inNode, { type: "escape" });
    expect(once).toMatchObject({ level: "node", open: false, userCollapsed: true });
    const twice = run(once, { type: "escape" });
    expect(twice).toMatchObject({ level: "chain", sel: chain });
    expect(run(twice, { type: "escape" })).toBe(twice);
  });
});
