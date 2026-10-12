import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../../types";
import { detail } from "../../item/testkit";
import { cardButtons } from "./actions";

const gate = (over: Partial<WorkItem> = {}) => detail({ display_status: "needs_you", stop: { kind: "gate", node: "final_review", resume_at: null, reason: null }, pending_gate: "final_review", ...over });
const kinds = (item: WorkItem) => cardButtons(item).map((b) => b.kind);

describe("cardButtons: a card's pair, each only where the server's actions admit it (F4)", () => {
  it.each<[string, WorkItem, string[]]>([
    ["a gate, an older server", gate(), ["approve", "reject"]],
    ["a gate, both listed", gate({ actions: ["approve", "reject"] }), ["approve", "reject"]],
    // An open must-fix: /approve refuses, /reject still takes it.
    ["a gate, the server refusing approve", gate({ actions: ["reject", "cancel"] }), ["reject"]],
    ["a gate, the server refusing reject", gate({ actions: ["approve"] }), ["approve"]],
    ["a paused item, the server refusing resume", detail({ display_status: "paused", actions: ["cancel"] }), []],
    ["a failed item, the server refusing retry", detail({ display_status: "failed", stop: { kind: "failed", node: "verification", resume_at: null, reason: null }, actions: ["escalate"] }), []],
  ])("%s", (_name, item, want) => expect(kinds(item)).toEqual(want));
});
