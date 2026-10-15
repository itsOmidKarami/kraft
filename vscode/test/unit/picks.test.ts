import { describe, expect, it } from "vitest";
import { gatePicks, itemPick, nextGate, pickableFor, type PickVerb } from "../../src/core/picks";
import { item, stopped } from "./fixtures/item";

const ids = (items: { id: string }[]) => items.map((i) => i.id);

describe("pickableFor", () => {
  it.each<[string, PickVerb, ReturnType<typeof item>[], string[]]>([
    ["retry: the server's door", "retry", [item({ id: "a", actions: ["retry"] }), item({ id: "b", actions: ["pause"] })], ["a"]],
    ["pause: the server's door", "pause", [item({ id: "a", actions: ["retry"] }), item({ id: "b", actions: ["pause"] })], ["b"]],
    ["openArtifact: a pending gate", "openArtifact", [stopped("gate", { id: "g", pending_gate: "spec_approval" }), item({ id: "r", actions: ["approve"] })], ["g"]],
    ["mr: a merge request on the web", "openMergeRequest", [item({ id: "m", mr_ref: { number: 1, url: "https://g/1" } }), item({ id: "x", mr_ref: { number: 2, url: "vscode://x" } })], ["m"]],
    ["answer: a question the server would resume", "answer", [stopped("question", { id: "q", actions: ["resume", "retry"] }), stopped("question", { id: "n", actions: ["retry"] }), item({ id: "p", display_status: "paused", actions: ["resume"] })], ["q"]],
    ["raiseBudget: the raise_budget door", "raiseBudget", [stopped("budget", { id: "b", actions: ["raise_budget", "retry"] }), stopped("budget", { id: "n", actions: ["retry"] })], ["b"]],
    ["openSpec: one filed with a spec", "openSpec", [item({ id: "s", attachments: [{ kind: "spec", path: "s.md" }] }), item({ id: "p", attachments: [{ kind: "plan", path: "p.md" }] }), item({ id: "n" })], ["s"]],
    ["openWorktree: one on disk", "openWorktree", [item({ id: "w", worktree_exists: true }), item({ id: "n", worktree_exists: false })], ["w"]],
    ["openInWebUi: every item", "openInWebUi", [item({ id: "a" }), item({ id: "b", actions: [] })], ["a", "b"]],
  ])("%s", (_, verb, items, want) => expect(ids(pickableFor(verb, items))).toEqual(want));
});

it("gatePicks lists the items at a gate, the longest-waiting first", () => {
  const gate = (id: string, at: string) => stopped("gate", { id, pending_gate: "spec_approval", updated_at: at });
  expect(ids(gatePicks([gate("b", "2026-10-12T10:00:00Z"), item({ id: "r" }), gate("a", "2026-10-12T09:00:00Z")]))).toEqual(["a", "b"]);
});

describe("nextGate", () => {
  const gate = (id: string, at: string) => stopped("gate", { id, pending_gate: "spec_approval", updated_at: at });
  it.each<[string, ReturnType<typeof item>[], string | undefined, string[]]>([
    ["one gate opens at once", [gate("a", "2026-10-12T09:00:00Z"), item({ id: "r" })], "a", ["a"]],
    ["several are asked about, the longest-waiting first", [gate("b", "2026-10-12T10:00:00Z"), gate("a", "2026-10-12T09:00:00Z")], undefined, ["a", "b"]],
    ["none is asked about", [item({ id: "r" })], undefined, []],
  ])("%s", (_, items, open, ask) => {
    const next = nextGate(items);
    expect(next.open?.id).toBe(open);
    expect(ids(next.ask)).toEqual(ask);
  });
});

it("itemPick shows the title, then the id and why", () =>
  expect(itemPick(stopped("budget", { id: "K-7", title: "Fix it" }))).toEqual({ label: "Fix it", description: "waiting for you at implementation", detail: "K-7", id: "K-7" }));
