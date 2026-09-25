import { describe, expect, it } from "vitest";
import { Announcer } from "../../src/core/announcer";

const gate = (id: string, g: string) => ({ id, status: "needs_human", pending_gate: g, current_node_id: "n" }) as any;
const paused = (id: string) => ({ id, status: "paused", current_node_id: "n" }) as any;
const running = (id: string) => ({ id, status: "active", current_node_id: "n" }) as any;

describe("Announcer", () => {
  it("announces a gate once", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(gate("A", "spec"))).toEqual({ kind: "gate", id: "A", gate: "spec" });
    expect(a.consider(gate("A", "spec"))).toBeUndefined();
  });

  it("announces the next gate on the same item", () => {
    const a = new Announcer(() => "all");
    a.consider(gate("A", "spec"));
    expect(a.consider(gate("A", "plan"))).toEqual({ kind: "gate", id: "A", gate: "plan" });
  });

  it("re-announces a needs-you state only after the item left it", () => {
    const a = new Announcer(() => "all");
    expect(a.consider(paused("A"))).toMatchObject({ kind: "needs", state: "paused" });
    expect(a.consider(paused("A"))).toBeUndefined();
    a.consider(running("A"));
    expect(a.consider(paused("A"))).toMatchObject({ kind: "needs", state: "paused" });
  });

  it("honours the notification level", () => {
    expect(new Announcer(() => "gates").consider(paused("A"))).toBeUndefined();
    expect(new Announcer(() => "gates").consider(gate("A", "g"))).toBeDefined();
    expect(new Announcer(() => "off").consider(gate("A", "g"))).toBeUndefined();
  });
});
