import { ngItem, type Case } from "../cellKit";

const ATT = [{ kind: "spec", path: "docs/specs/doc-search-cache.md" }, { kind: "plan", path: "docs/plans/doc-search-cache.md" }];
const doc = (kind: string, n: number) => ({
  document_id: `att-${kind}`, repo: "/Users/dev/code/kraft-plugins", title: `Doc search cache ${kind}`, kind, source_kind: "spec",
  path: `docs/${kind}s/doc-search-cache.md`, node_id: null, hook_point: null, worker_session_id: null, attachment_kind: kind, indexed_at: `2026-09-13T08:0${n}:00Z`,
});

export const cells: Case[] = [
  // Kraft-9d8b2.29: a spec and a plan attached at intake read in the chain pane's Overview, each opening its document.
  { screen: "ng-item", variant: "attached", data: "default", widths: [1280], run: async (c) => {
    const id = c.S.ng.running;
    const it = c.S.bundles[id].item;
    const before = c.S.docs[id];
    it.attachments = ATT;
    c.S.docs[id] = [doc("spec", 1), doc("plan", 2)];
    try { await ngItem(c, "running"); } finally { it.attachments = []; c.S.docs[id] = before; }
  } },
];
