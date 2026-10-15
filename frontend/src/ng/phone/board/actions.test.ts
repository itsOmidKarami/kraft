import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../../types";
import { detail } from "../../item/testkit";
import { cardButtons } from "./actions";

const gate = (over: Partial<WorkItem> = {}) => detail({ display_status: "needs_you", stop: { kind: "gate", node: "final_review", resume_at: null, reason: null }, pending_gate: "final_review", ...over });
const kinds = (item: WorkItem) => cardButtons(item).map((b) => b.kind);

describe("cardButtons: a card's button, only where the server's actions admit one (F4)", () => {
  it.each<[string, WorkItem, string[]]>([
    ["a gate, an older server", gate(), ["review"]],
    ["a gate, both listed", gate({ actions: ["approve", "reject"] }), ["review"]],
    // An open must-fix: /approve refuses, /reject still takes it; the review is still the door.
    ["a gate, the server refusing approve", gate({ actions: ["reject", "cancel"] }), ["review"]],
    ["a gate, the server refusing both", gate({ actions: ["cancel"] }), []],
    ["a paused item, the server refusing resume", detail({ display_status: "paused", actions: ["cancel"] }), []],
    ["a failed item, the server refusing retry", detail({ display_status: "failed", stop: { kind: "failed", node: "verification", resume_at: null, reason: null }, actions: ["escalate"] }), []],
  ])("%s", (_name, item, want) => expect(kinds(item)).toEqual(want));
});
