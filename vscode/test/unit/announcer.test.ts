import { describe, expect, it } from "vitest";
import type { DisplayStatus } from "../../../frontend/src/types";
import { Announcer, toastActions, toastLevel, toastPlan } from "../../src/core/announcer";
import { announceable } from "../../src/core/board";
import { WorkspaceScope, type ScopeDeps } from "../../src/core/repoScope";
import { item, stopped } from "./fixtures/item";

const gate = (g: string) => stopped("gate", { pending_gate: g });
const question = stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } });

describe("Announcer", () => {
  it("announces a gate once", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(gate("spec"))).toEqual({ kind: "gate", id: "K-1", gate: "spec", text: "approve spec" });
    expect(a.consider(gate("spec"))).toBeUndefined();
  });

  it("announces the next gate on the same item", () => {
    const a = new Announcer(() => "all");
    a.consider(gate("spec"));
    expect(a.consider(gate("plan"))).toEqual({ kind: "gate", id: "K-1", gate: "plan", text: "approve plan" });
  });

  it("re-announces a needs-you stop only after the item left it", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(question)).toEqual({ kind: "needs", id: "K-1", text: "agent asks: Keep the header?" });
    expect(a.consider(question)).toBeUndefined();
    a.consider(item());
    expect(a.consider(question)).toBeDefined();
  });

  it("an escalation in between does not re-announce the same stop", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(question)).toBeDefined();
    a.consider(item({ display_status: "escalated" }));
    expect(a.consider(question)).toBeUndefined();
    a.consider(item({ display_status: "escalated" }));
    expect(a.consider(stopped("cap"))).toBeDefined();
  });

  it("re-announces when the stop kind changes", () => {
    const a = new Announcer(() => "all");
    a.consider(stopped("cap"));
    expect(a.consider(stopped("question"))).toBeDefined();
  });

  it("announces a failure", () => {
    const failed = item({ display_status: "failed", stop: { kind: "failed", node: "implementation", reason: "boom", resume_at: null } });
    expect(new Announcer(() => "all").consider(failed)).toEqual({ kind: "needs", id: "K-1", text: "failed at implementation" });
  });

  it("announces a failure with a stale pending gate as a failure, not a gate", () => {
    const failed = item({ display_status: "failed", pending_gate: "spec", stop: { kind: "failed", node: "implementation", reason: null, resume_at: null } });
    expect(new Announcer(() => "all").consider(failed)).toMatchObject({ kind: "needs" });
  });

  it.each<DisplayStatus>(["paused", "escalated", "queued", "running", "waiting", "blocked", "done", "cancelled"])("%s never toasts", (d) => {
    expect(new Announcer(() => "all").consider(item({ display_status: d }))).toBeUndefined();
  });

  it("never toasts an archived row", () => {
    expect(new Announcer(() => "all").consider(stopped("cap", { display_status: "archived", archived_at: "x" }))).toBeUndefined();
    expect(new Announcer(() => "all").consider(stopped("cap", { archived_at: "x" }))).toBeUndefined();
  });

  it("honours the notification level", () => {
    expect(new Announcer(() => "gates").consider(question)).toBeUndefined();
    expect(new Announcer(() => "gates").consider(gate("g"))).toBeDefined();
    expect(new Announcer(() => "off").consider(gate("g"))).toBeUndefined();
  });
});

describe("toasts follow the window's scope", () => {
  // No git: each folder scopes to itself.
  const noGit: ScopeDeps = { git: async () => undefined, realpath: async (p) => p, platform: "linux" };

  it("a gate already open toasts once when the scope is ready; widening adds no toasts but a new gate does", async () => {
    const scope = new WorkspaceScope("linux");
    const a = new Announcer(() => "all");
    const here = gate("spec");
    const there = stopped("gate", { id: "K-2", repo: "/other", pending_gate: "spec" });
    let items = [here, there];
    const inScope = () => announceable(items, (i) => scope.includes(i, () => undefined));
    const ids = (found: [{ id: string }, unknown][]) => found.map(([i]) => i.id);
    // A store change, and a scope change, as gates.ts wires them.
    const stored = () => ids(a.considerAll(inScope()));
    const rescoped = () => ids(a.rescope(inScope(), scope.showAll));

    const toasts = [stored()];
    await scope.update(["/r"], "/k", noGit);
    toasts.push(rescoped(), rescoped());
    scope.toggleAll();
    toasts.push(rescoped(), stored());
    scope.toggleAll();
    toasts.push(rescoped());
    scope.toggleAll();
    toasts.push(rescoped());
    items = [...items, stopped("gate", { id: "K-3", repo: "/other", pending_gate: "plan" })];
    toasts.push(stored());
    expect(toasts).toEqual([[], ["K-1"], [], [], [], [], [], ["K-3"]]);
  });
});

describe("toastActions", () => {
  const web = { label: "Open in Web UI", command: "kraft.openInWebUi" };
  const answer = { label: "Answer", command: "kraft.answer" };
  const retry = { label: "Retry", command: "kraft.retry" };
  it.each([
    ["a question offers Answer, running kraft.answer, when the server admits resume", stopped("question", { actions: ["resume", "cancel"] }), false, [web, answer]],
    ["a question offers no Answer once resume is gone", stopped("question", { actions: ["cancel"] }), false, [web]],
    ["a question from a server without actions offers none", stopped("question"), false, [web]],
    ["a read-only window keeps only the web door on a question", stopped("question", { actions: ["resume"] }), true, [web]],
    ["a cap keeps Retry, running kraft.retry", stopped("cap", { actions: ["resume", "retry"] }), false, [web, retry]],
    ["a failed item keeps Retry", item({ display_status: "failed", status: "needs_human" }), false, [web, retry]],
    ["a read-only window keeps only the web door on a failure", item({ display_status: "failed", status: "needs_human" }), true, [web]],
  ])("%s", (_, it_, readOnly, want) => expect(toastActions(it_, readOnly)).toEqual(want));
});

it.each([
  ["a failed item", item({ display_status: "failed", stop: { kind: "failed", node: "implementation", reason: "x", resume_at: null } }), "warning"],
  ["a cap", stopped("cap"), "warning"],
  ["a budget stop", stopped("budget"), "warning"],
  ["a stop that needs a human", stopped("conflict"), "warning"],
  ["a question", question, "information"],
  ["a gate", gate("spec"), "information"],
])("toastLevel: %s", (_, it_, want) => expect(toastLevel(it_)).toBe(want));

describe("toastPlan", () => {
  const g = (id: string) => [item({ id }), { kind: "gate", id, gate: "g", text: "approve g" }] as const;
  const fail = [item({ id: "F", display_status: "failed", stop: { kind: "failed", node: "n", reason: "x", resume_at: null } }), { kind: "needs", id: "F", text: "failed at n" }] as const;
  it.each([
    ["none: nothing", [], { each: [] }],
    ["one: as it is", [g("a")], { each: [g("a")] }],
    ["two gates: one summary, Next Gate and Show Board offered", [g("a"), g("b")], { summary: "2 items need you", level: "information", buttons: ["Next Gate", "Show Board"] }],
    ["a fault among them: a warning", [g("a"), fail], { summary: "2 items need you", level: "warning", buttons: ["Next Gate", "Show Board"] }],
    ["stops only: Show Board only", [fail, fail], { summary: "2 items need you", level: "warning", buttons: ["Show Board"] }],
  ])("%s", (_, found, want) => expect(toastPlan(found.map((f) => [...f]) as never)).toEqual(want));
});
