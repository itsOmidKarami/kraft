import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../../types";
import { draftBody, emptyDraft } from "./draftBody";

const N = (id: string, more: Partial<ChainNode> = {}) => ({ id, kind: "exec", tasks: [], gate_after: null, ...more }) as ChainNode;
const NODES = [
  N("spec", { covered_by: "spec" }),
  N("review_gate", { kind: "gate", auto_escalate: true }),
  N("plain_gate", { kind: "gate", auto_escalate: false }),
  N("verification", { fix_loop: "verification.fix_loop" }),
  N("mr_checks", { fix_loop: "mr_checks.fix_loop" }),
];
const base = { title: " Cache it ", brief: " Key by hash. ", repo: "/code/kraft-plugins", chain: "default" };

describe("draftBody", () => {
  it("sends the plain create body when nothing is overridden", () => {
    expect(draftBody(emptyDraft(base), NODES, null, false)).toEqual({
      title: "Cache it", description: "Key by hash.", repo: "/code/kraft-plugins", chain_template: "default",
      attachments: [], skip_nodes: [], auto_gate: true, autostart: false,
    });
  });

  it("sends attachments, skips, a set budget (not a blank one), agent review off, and autostart", () => {
    const b = draftBody(emptyDraft({ ...base, spec: "docs/spec.md", plan: " ", skip: ["mr_checks"], budget: "$7.50", autoGate: false }), NODES, null, true);
    expect(b).toMatchObject({ attachments: [{ kind: "spec", path: "docs/spec.md" }], skip_nodes: ["mr_checks"], budget_usd: 7.5, auto_gate: false, autostart: true });
    expect("budget_usd" in draftBody(emptyDraft({ ...base, budget: "  " }), NODES, null, false)).toBe(false);
  });

  it("spreads item-wide fix caps over fix-loop nodes without their own, a node's own value winning, none on a skipped node", () => {
    const b = draftBody(emptyDraft({ ...base, attempts: "4", wallMin: "45", nodeAttempts: { verification: "6" }, skip: ["mr_checks"] }), NODES, null, false);
    expect(b.node_overrides).toEqual({ verification: { attempts: 6, wall_clock_s: 2700 } });
    const c = draftBody(emptyDraft({ ...base, attempts: "4" }), NODES, null, false);
    expect(c.node_overrides).toEqual({ verification: { attempts: 4 }, mr_checks: { attempts: 4 } });
  });

  it("auto-escalates only the gates that declare a reviewer", () => {
    expect(draftBody(emptyDraft({ ...base, autoEscalate: true }), NODES, null, false).node_overrides).toEqual({ review_gate: { auto_escalate: true } });
  });

  it("names the workspace, members and pointer only when members are picked", () => {
    expect(draftBody(emptyDraft({ ...base, members: ["kraft-lite"], pointer: "bump" }), NODES, "plugins-ws", false)).toMatchObject({ workspace: "plugins-ws", members: ["kraft-lite"], root_pointer_policy: "bump" });
    expect("workspace" in draftBody(emptyDraft(base), NODES, "plugins-ws", false)).toBe(false);
  });
});
