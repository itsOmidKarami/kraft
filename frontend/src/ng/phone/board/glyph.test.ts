import { describe, expect, it } from "vitest";
import type { WorkItem } from "../../../types";
import { stateGlyph } from "./glyph";

const row = (display_status: string, stop?: string, node_kind: "gate" | "exec" = "exec") =>
  ({ id: "w", display_status, status: "active", stop: stop ? { kind: stop, node: "n", resume_at: null, reason: null } : null, current_node_id: "n", chain_definition: { nodes: [{ id: "n", kind: node_kind, steps: [["a"], ["b"]] }] } }) as unknown as WorkItem;

describe("a phone row's glyph says the state, not the node kind (PH-11)", () => {
  it.each([
    ["a gate wait", row("needs_you", "gate", "gate"), "flag"],
    ["a cap", row("needs_you", "cap"), "ban"],
    ["a budget stop", row("needs_you", "budget"), "ban"],
    ["a question", row("needs_you", "question"), "message-square"],
    ["a failure", row("failed", "failed"), "circle-alert"],
    ["a running item at a multi-step node", row("running"), "circle-dot"],
    ["a paused item", row("paused"), "pause"],
    ["an escalation", row("escalated"), "siren"],
    ["a done item", row("done"), "check"],
  ])("%s", (_, item, icon) => {
    const g = stateGlyph(item);
    expect(g.icon).toBe(icon);
    expect(g.kind).toBe("exec");
  });
});
