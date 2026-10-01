import { ngItem, type Case } from "../cellKit";

export const cells: Case[] = [
  // Kraft-9d8b2.7: the worktree was reclaimed, so ⋮ keeps Open worktree but disabled, with the Retry hint.
  { screen: "ng-item", variant: "kebab-no-worktree", data: "default", widths: [1280], run: async (c) => {
    const it = c.S.bundles[c.S.ng.running].item;
    it.worktree_exists = false;
    try { await ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Item menu" }).click(); await p.getByText(/worktree removed/).waitFor(); } }); }
    finally { delete it.worktree_exists; }
  } },
];
