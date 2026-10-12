import { describe, expect, it } from "vitest";
import type { DisplayStatus } from "../../../frontend/src/types";
import { Announcer } from "../../src/core/announcer";
import { item, stopped } from "./fixtures/item";

const gate = (g: string) => stopped("gate", { pending_gate: g });
const question = stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } });

describe("Announcer", () => {
  it("announces a gate once", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(gate("spec"))).toEqual({ kind: "gate", id: "K-1", gate: "spec" });
    expect(a.consider(gate("spec"))).toBeUndefined();
  });

  it("announces the next gate on the same item", () => {
    const a = new Announcer(() => "all");
    a.consider(gate("spec"));
    expect(a.consider(gate("plan"))).toEqual({ kind: "gate", id: "K-1", gate: "plan" });
  });

  it("re-announces a needs-you stop only after the item left it", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(question)).toEqual({ kind: "needs", id: "K-1", text: "agent asks: Keep the header?" });
    expect(a.consider(question)).toBeUndefined();
    a.consider(item());
    expect(a.consider(question)).toBeDefined();
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
