import { describe, expect, it } from "vitest";
import type { Harnesses } from "../../types";
import { capAt, effortOf, effortOptions, materialized, modelOf, modelSuggestions, notStarted, oneOf, taskAt, type MTask } from "./chainValues";
import { detail, FROZEN } from "./testkit";

const m = materialized({ materialized_chain: FROZEN })!;
const h = {
  profiles: [{ id: "claude", provider: "claude", defaults: { model: "sonnet", effort: "medium" } }, { id: "work", provider: "codex", defaults: {} }],
  agent_profiles: [{ id: "strong", effort: "high", model: { claude: "opus-4", codex: "gpt-5" } }],
} as unknown as Harnesses;
const task = (over: Partial<MTask>): MTask => ({ id: "t", kind: "agent", harness: "claude", ...over });

describe("chainValues", () => {
  it("is not started only before any node ran and while the item is open", () => {
    expect(notStarted(detail({ current_node_id: null, display_status: "paused" }))).toBe(true);
    expect(notStarted(detail({ current_node_id: "plan" }))).toBe(false);
    expect(notStarted(detail({ current_node_id: null, display_status: "cancelled" }))).toBe(false);
  });

  it("reads no chain from a missing or broken snapshot", () => {
    expect(materialized({ materialized_chain: null })).toBeNull();
    expect(materialized({ materialized_chain: "{not json" })).toBeNull();
  });

  it("finds a task by path, a `tasks:` node's under the step `main`", () => {
    expect(taskAt(m, "verification.review.code_review")?.model).toBe("opus");
    const plain = materialized({ materialized_chain: JSON.stringify({ chain: { nodes: [{ id: "n", kind: "exec", tasks: [{ id: "x", kind: "builtin" }] }] } }) })!;
    expect(taskAt(plain, "n.main.x")?.kind).toBe("builtin");
  });

  it.each([
    ["the task's own cap", "verification.checks.lint", "time_cap_minutes", 10],
    ["the level's default, held to the nearest broader maximum", "verification.review.code_review", "time_cap_minutes", 30],
    ["a node's cap, inherited by its task", "merge_request.open.open_draft", "budget_usd", 2],
    ["no cap at all", "plan.write.plan", "token_budget", null],
  ])("caps: %s", (_n, path, key, want) => expect(capAt(m, path, key)).toBe(want));

  it.each([
    ["its own model", task({ model: "haiku" }), "haiku"],
    ["its profile's model for the harness's provider", task({ profile: "strong" }), "opus-4 · strong profile"],
    ["a profile that names none for the provider", task({ harness: "gemini", profile: "strong" }), "strong profile"],
    ["the harness's default", task({}), "sonnet · claude default"],
  ])("model: %s", (_n, t, want) => expect(modelOf(t, h)).toBe(want));

  it("effort: its own, its profile's, else the harness's default", () => {
    expect(effortOf(task({ effort: "low" }), h)).toBe("low");
    expect(effortOf(task({ profile: "strong" }), h)).toBe("high · strong profile");
    expect(effortOf(task({}), h)).toBe("medium · claude default");
  });

  it("says one value for many tasks only when they agree", () => {
    expect(oneOf(["a", "a"])).toBe("a");
    expect(oneOf(["a", "b"])).toBe("each task's own");
  });

  it("suggests the provider's models, its agent profiles' and its harnesses' defaults, once each", () => {
    expect(modelSuggestions(["claude"], [{ id: "claude", models: ["sonnet", "claude-x"] }], h)).toEqual(["sonnet", "claude-x", "opus-4"]);
  });

  it("offers only the efforts every provider accepts", () => {
    const listed = [{ id: "claude", efforts: ["low", "high", "max"] }, { id: "codex", efforts: ["low", "high", "minimal"] }, { id: "gemini", efforts: [] }];
    expect(effortOptions(["claude", "codex", "gemini"], listed)).toEqual(["low", "high"]);
    expect(effortOptions(["gemini"], listed)).toEqual([]);
  });
});
