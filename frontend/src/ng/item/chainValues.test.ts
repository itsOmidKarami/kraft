import { describe, expect, it } from "vitest";
import type { Harnesses } from "../../types";
import { agentTasks, attemptsAt, capAt, capText, effortOf, effortOptions, materialized, modelOf, modelSuggestions, notStarted, oneOf, taskAt, type MTask } from "./chainValues";
import { API_ITEM, SERVER_ATTEMPTS, SERVER_CAPS } from "./fixture.api";
import { detail, FROZEN } from "./testkit";

const m = materialized({ materialized_chain: FROZEN })!;
const h = {
  profiles: [{ id: "claude", provider: "claude", defaults: { model: "sonnet", effort: "medium" } }, { id: "work", provider: "codex", defaults: {} }],
  agent_profiles: [
    { id: "strong", effort: "high", model: { claude: "opus-4", codex: "gpt-5" }, providers: { claude: { model: "opus-4", effort: "high" }, codex: { model: "gpt-5", effort: "high" } } },
    // Efforts that differ by provider: the profile's own `effort` is null.
    { id: "mixed", effort: null, model: { claude: "opus-4", codex: "gpt-5" }, providers: { claude: { model: "opus-4", effort: "max" }, codex: { model: "gpt-5", effort: "low" } } },
  ],
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
    ["the task's own cap", "verification.checks.lint", "time_cap_minutes", { value: 10, source: "chain" }],
    ["the level's default, held to the nearest broader maximum", "verification.review.code_review", "time_cap_minutes", { value: 30, source: "policy maximum" }],
    ["a node's cap, inherited by its task", "merge_request.open.open_draft", "budget_usd", { value: 2, source: "chain" }],
    ["no cap at all", "plan.write.plan", "token_budget", { value: null, source: "policy" }],
  ])("caps: %s", (_n, path, key, want) => expect(capAt(m, path, key)).toEqual(want));

  it.each(Object.entries(SERVER_CAPS).flatMap(([path, caps]) => Object.entries(caps).map(([key, want]) => [path, key, want] as const)))(
    "caps match the server's resolution with the item's own policy: %s %s",
    (path, key, want) => {
      const real = materialized(API_ITEM as { materialized_chain: string })!;
      expect(capAt(real, path, key, API_ITEM.policy_override).value).toBe(want);
    },
  );

  it("names the item's own policy as the source of a cap it set", () => {
    const real = materialized(API_ITEM as { materialized_chain: string })!;
    expect(capAt(real, "plan.main.author", "time_cap_minutes", API_ITEM.policy_override).source).toBe("item policy");
  });

  it("reads the fix-loop attempts from the item's own policy first, as the server does", () => {
    const real = materialized(API_ITEM as { materialized_chain: string })!;
    expect(attemptsAt(real, "verification", API_ITEM.policy_override)).toEqual({ value: String(SERVER_ATTEMPTS.verification), source: "item policy" });
    expect(attemptsAt(real, "verification", null)).toEqual({ value: "2", source: "chain" });
  });

  it("counts a node's fix-loop repair and judge among its agent tasks", () => {
    const real = materialized(API_ITEM as { materialized_chain: string })!;
    expect(agentTasks(real, "verification").map((t) => t.id)).toEqual(["code_review", "repair", "judge"]);
  });

  it.each([
    ["its profile's model for the harness's provider", task({ profile: "strong" }), { value: "opus-4 · strong", source: "profile" }],
    ["its own", task({ model: "haiku" }), { value: "haiku", source: "chain" }],
    ["the repo's model for its harness", task({}), { value: "repo-model", source: "repo" }],
    ["the harness's default", task({ harness: "work" }), { value: "work default", source: "harness" }],
  ])("model: %s", (_n, t, want) => expect(modelOf(t, h, { models: { claude: "repo-model" } })).toEqual(want));

  it("model: the harness default when the repo sets none", () => {
    expect(modelOf(task({}), h, null)).toEqual({ value: "sonnet", source: "harness" });
  });

  it("effort: its profile's for the provider, even where the profile's own differs by provider", () => {
    expect(effortOf(task({ profile: "mixed" }), h)).toEqual({ value: "max · mixed", source: "profile" });
    expect(effortOf(task({ harness: "work", profile: "mixed" }), h)).toEqual({ value: "low · mixed", source: "profile" });
    expect(effortOf(task({ effort: "low" }), h)).toEqual({ value: "low", source: "chain" });
    expect(effortOf(task({}), h)).toEqual({ value: "medium", source: "harness" });
  });

  it("says one value for many tasks only when they agree", () => {
    expect(oneOf([{ value: "a", source: "chain" }, { value: "a", source: "chain" }])).toEqual({ value: "a", source: "chain" });
    expect(oneOf([{ value: "a", source: "chain" }, { value: "b", source: "chain" }]).value).toBe("each task's own");
  });

  it("suggests the provider's models, its agent profiles' and its harnesses' defaults, once each", () => {
    expect(modelSuggestions(["claude"], [{ id: "claude", models: ["sonnet", "claude-x"] }], h)).toEqual(["sonnet", "claude-x", "opus-4"]);
  });

  it("offers only the efforts every provider accepts", () => {
    const listed = [{ id: "claude", efforts: ["low", "high", "max"] }, { id: "codex", efforts: ["low", "high", "minimal"] }, { id: "gemini", efforts: [] }];
    expect(effortOptions(["claude", "codex", "gemini"], listed)).toEqual(["low", "high"]);
    expect(effortOptions(["gemini"], listed)).toEqual([]);
  });

  it("writes dollars to the cent", () => {
    expect(capText("budget_usd", 1.5)).toBe("$1.50");
    expect(capText("time_cap_minutes", 20)).toBe("20m");
    expect(capText("token_budget", null)).toBe("no cap");
  });
});
