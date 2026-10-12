import { describe, expect, it } from "vitest";
import { clickTarget, primaryAction, primaryToken, rowCommand } from "../../src/core/primary";
import { item, stopped } from "./fixtures/item";

describe("primaryAction", () => {
  it.each([
    ["gate", stopped("gate", { pending_gate: "spec_approval" }), "openArtifact"],
    ["question", stopped("question", { actions: ["resume", "retry"] }), "answer"],
    ["question without resume", stopped("question", { actions: ["retry"] }), null],
    ["budget", stopped("budget", { actions: ["raise_budget", "retry"] }), "raiseBudget"],
    ["budget without raise_budget", stopped("budget", { actions: ["retry"] }), null],
    ["stuck", stopped("stuck", { actions: ["retry"] }), "retry"],
    ["failed", item({ display_status: "failed", actions: ["retry"] }), "retry"],
    ["failed without retry", item({ display_status: "failed", actions: [] }), null],
    ["paused", item({ display_status: "paused", actions: ["resume"] }), "resume"],
    ["paused without resume", item({ display_status: "paused", actions: [] }), null],
    ["paused, never started", item({ display_status: "paused", current_node_id: null, actions: ["resume"] }), null],
    ["cap", stopped("cap"), "openInWebUi"],
    ["conflict", stopped("conflict"), "openInWebUi"],
    ["running", item({ actions: ["pause"] }), null],
  ])("%s", (_, it_, want) => expect(primaryAction(it_)).toBe(want));
});

it.each([
  ["openArtifact", "primaryOpenArtifact"],
  ["raiseBudget", "primaryRaiseBudget"],
  ["openInWebUi", "primaryOpenInWebUi"],
] as const)("%s's token is %s", (p, want) => expect(primaryToken(p)).toBe(want));

describe("clickTarget", () => {
  it.each([
    ["gate", stopped("gate", { pending_gate: "spec_approval" }), "openArtifact"],
    ["question", stopped("question", { actions: ["resume"] }), "openInWebUi"],
    ["running", item(), "openInWebUi"],
  ])("%s", (_, it_, want) => expect(clickTarget(it_)).toBe(want));
});

describe("rowCommand", () => {
  it.each([
    ["a gate opens its document", stopped("gate", { pending_gate: "spec_approval" }), { command: "kraft.openArtifact" }],
    ["a question only selects", stopped("question", { actions: ["resume"] }), undefined],
    ["a running row only selects", item(), undefined],
  ])("%s", (_, it_, want) => expect(rowCommand(it_)).toEqual(want));
});
