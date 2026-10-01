import { NG_SCENARIOS } from "../ngItems";
import { ngItem, type Case } from "../cellKit";

export const cells: Case[] = [
  // ux2-W5: the item page, every scenario the prototype draws (plus paused), at 1280 and with long data.
  // Three of them also at 1024 and 1920, light and 700px tall.
  ...NG_SCENARIOS.map<Case>((sc) => {
    const wide = ["running", "failed", "needs-gate"].includes(sc);
    return { screen: "item", variant: sc, data: "default", widths: wide ? [1024, 1280, 1920] : [1280], ...(wide ? { shells: [{ mode: "light" }, { short: true }] } : {}), run: (c) => ngItem(c, sc) };
  }),
  // Under 1024 the pane overlays the canvas (R7) and the current node stays clear of it (Kraft-gvfm2).
  { screen: "item", variant: "running", data: "default", widths: [768], run: (c) => ngItem(c, "running", { side: "rail" }) },
  { screen: "item", variant: "chain-config", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "?tab=config" }) },
  { screen: "item", variant: "chain-config-capped-long", data: "long", widths: [1280], shells: [{ short: true }], run: (c) => ngItem(c, "capped", { tail: "?tab=config" }) },
  // ux2-W5 G: the node view (strip, node canvas, node pane).
  { screen: "item-node", variant: "running", data: "default", widths: [1024, 1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification" }) },
  { screen: "item-node", variant: "needs-you", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-you", { tail: "/nodes/verification" }) },
  { screen: "item-node", variant: "failed", data: "default", widths: [1280], run: (c) => ngItem(c, "failed", { tail: "/nodes/merge_request" }) },
  // ux2-W5 H: the task pane's tabs, on the attempt the URL names.
  { screen: "item-task", variant: "overview", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.review.code_review" }) },
  { screen: "item-task", variant: "log", data: "default", widths: [1280], shells: [{ short: true }], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.review.code_review&tab=log" }) },
  { screen: "item-task", variant: "log-long", data: "long", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks.lint&tab=log&attempt=1" }) },
  { screen: "item-task", variant: "output", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks.lint&tab=output" }) },
  { screen: "item-task", variant: "thread", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-you", { tail: "/nodes/verification?sel=verification.escalation.escalation" }) },
  { screen: "item-step", variant: "parallel", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks" }) },
  // ux2-W5 I: the gate's pane (decision card) and its node view.
  { screen: "item-gate", variant: "pane", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review" }) },
  { screen: "item-gate", variant: "view", data: "default", widths: [1024, 1280], run: (c) => ngItem(c, "needs-gate", { tail: "/nodes/final_review" }) },
  { screen: "item-gate", variant: "passed", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "?sel=plan_approval" }) },
  { screen: "item-gate", variant: "reject", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review", then: async (p) => { await p.getByRole("button", { name: "Reject…" }).click(); } }) },
  // ux2-W5 J: the document viewer, on the pending gate's document.
  { screen: "item-doc", variant: "artifact", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review", then: async (p) => { await p.getByRole("button", { name: /^Read / }).click(); await p.getByRole("dialog").waitFor(); } }) },
  // The header's floating parts, opened the way a keyboard user would.
  { screen: "item", variant: "panel", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "More actions" }).focus(); } }) },
  { screen: "item", variant: "kebab", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Item menu" }).click(); } }) },
  { screen: "item", variant: "cancel-card", data: "default", widths: [1280], run: (c) => ngItem(c, "mr-closed", { then: async (p) => { await p.getByRole("button", { name: "Item menu" }).click(); await p.getByRole("menuitem", { name: /Cancel/ }).click(); await p.getByText(/stays on the ledger/).waitFor(); } }) },
  ...NG_SCENARIOS.map<Case>((sc) => ({ screen: "item", variant: `${sc}-long`, data: "long", widths: [1280], run: (c) => ngItem(c, sc) })),
];
