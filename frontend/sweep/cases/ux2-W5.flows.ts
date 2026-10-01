import { expect } from "@playwright/test";
import { ngItem, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // ux2-W5 B.9: Cancel… reached by keyboard only, and the request is /cancel (never the route that deletes the worktree), R17.
  { name: "cancel", widths: [1280], keyboard: true, start: ngItem("running"), steps: [
    { name: "toggle-focus-opens-panel", run: async (p) => { await p.getByRole("button", { name: "More actions" }).focus(); await expect(p.getByRole("menu", { name: "Item actions" })).toBeVisible(); } },
    { name: "arrows-to-cancel", run: async (p) => { await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("menuitem", { name: /Cancel/ })).toBeFocused(); } },
    { name: "enter-opens-card", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("dialog", { name: "Cancel this item?" })).toBeVisible(); await p.getByText(/stays on the ledger/).waitFor(); } },
    { name: "reason", run: async (p) => { await expect(p.getByLabel("Reason")).toBeFocused(); await p.keyboard.type("Superseded by kraft-cb61."); } },
    { name: "cancel-item", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items\/[^/]+\/(cancel|abandon)$/.test(r.url()));
      await p.keyboard.press("Tab"); await p.keyboard.press("Enter");
      const r = await sent;
      expect(new URL(r.url()).pathname).toMatch(/\/cancel$/);
      expect(r.postDataJSON()).toEqual({ reason: "Superseded by kraft-cb61.", close_mr: false });
      await expect(p.getByText("CANCELLED", { exact: true })).toBeVisible();
    } },
  ] },
  // ux2-W5 E: the pane by keyboard; the collapse a person chose survives picking nodes and moving to another item.
  { name: "pane-collapse", widths: [1280], start: ngItem("running"), steps: [
    { name: "collapse", run: async (p) => { await p.getByRole("button", { name: "Collapse pane" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeFocused(); }, kbd: true },
    // A click picks without opening (Enter opens, R6).
    { name: "pick-node-stays-collapsed", run: async (p) => { await p.getByRole("button", { name: /^implementation, node/ }).click(); await expect(p.getByRole("complementary", { name: "implementation pane, collapsed" })).toBeVisible(); } },
    { name: "other-item-stays-collapsed", run: async (p, S) => { await p.evaluate((url) => { history.pushState({}, "", url); dispatchEvent(new PopStateEvent("popstate")); }, `/work-items/${S.ng.failed}`); await expect(p.getByText("FAILED", { exact: true })).toBeVisible(); await expect(p.getByRole("button", { name: "Expand pane" })).toBeVisible(); } },
    { name: "rail-expands", run: async (p) => { await p.getByRole("button", { name: "Expand pane" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Collapse pane" })).toBeVisible(); }, kbd: true },
    { name: "escape-collapses", run: async (p) => { await p.getByRole("tab", { name: "Overview" }).focus(); await p.keyboard.press("Escape"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeFocused(); }, kbd: true },
  ] },
  // ux2-W5 G (R6): chain → node view → a task's pane → back, keyboard only.
  { name: "node-keyboard", widths: [1280], keyboard: true, start: ngItem("running"), steps: [
    { name: "tab-into-chain", run: async (p) => { await p.locator('.graph-node[tabindex="0"]').focus(); await expect(p.getByRole("button", { name: /^verification, node, running/ })).toBeFocused(); } },
    { name: "cmd-enter-node-view", run: async (p) => { await p.keyboard.press("ControlOrMeta+Enter"); await expect(p).toHaveURL(/\/nodes\/verification$/); await expect(p.getByRole("group", { name: "verification" })).toBeVisible(); await expect(p.locator('.graph-node[tabindex="0"]').first()).toBeFocused(); } },
    { name: "arrows-to-a-task", run: async (p) => { await p.keyboard.press("ArrowRight"); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("button", { name: /^typecheck,/ })).toBeFocused(); } },
    { name: "enter-opens-pane", run: async (p) => { await p.keyboard.press("Enter"); await expect(p).toHaveURL(/sel=verification\.checks\.typecheck/); await expect(p.getByRole("complementary", { name: "typecheck pane" })).toBeVisible(); } },
    // Escape from the canvas collapses the pane and leaves focus on the canvas.
    { name: "escape-collapses", run: async (p) => { await p.getByRole("button", { name: /^typecheck,/ }).focus(); await p.keyboard.press("Escape"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeVisible(); await expect(p.getByRole("button", { name: /^typecheck,/ })).toBeFocused(); } },
    { name: "escape-back-to-chain", run: async (p) => { await p.keyboard.press("Escape"); await expect(p).toHaveURL(/\/work-items\/[0-9a-f]+$/); await expect(p.locator('.graph-node[tabindex="0"]').first()).toBeFocused(); } },
  ] },
  // ux2-W5 H: retry a failed item from its failed task, through the task pane; the request names the task's path.
  { name: "retry-task", widths: [1280], start: ngItem("failed"), steps: [
    { name: "open-node", run: async (p) => { await p.getByRole("button", { name: "Open merge_request →" }).click(); await expect(p.getByRole("complementary", { name: "merge_request pane" })).toBeVisible(); } },
    { name: "focus", run: async (p) => { await p.getByRole("button", { name: /Focus/ }).click(); await expect(p).toHaveURL(/\/nodes\/merge_request$/); } },
    { name: "task-pane", run: async (p) => { await p.getByRole("button", { name: /^open_draft,/ }).click(); await expect(p.getByRole("complementary", { name: "open_draft pane" })).toBeVisible(); await expect(p.getByText("attempt 3 of 3")).toBeVisible(); } },
    { name: "retry-confirm", run: async (p) => { await p.getByRole("complementary", { name: "open_draft pane" }).getByRole("button", { name: "Retry" }).click(); await expect(p.getByRole("group", { name: "Retry merge_request.open.open_draft" })).toBeVisible(); } },
    { name: "retry-sent", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/retry"));
      await p.getByRole("group", { name: "Retry merge_request.open.open_draft" }).getByRole("button", { name: "Retry" }).click();
      expect((await sent).postDataJSON()).toEqual({ path: "merge_request.open.open_draft" });
    } },
  ] },
];
