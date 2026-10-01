import { expect, type Page } from "@playwright/test";
import { NG_NOW } from "../ngItems";
import { ng, type Flow } from "../flowKit";

// ux2-W6: the /ng board on its own fixtures (`mock: { ngBoard: true }`).
const ngBoard = (tail = "") => async (p: Page) => { await p.clock.setFixedTime(new Date(NG_NOW)); await ng(`/ng/${tail}`)(p); };

export const flows: Flow[] = [
  // ux2-W6 D.4 (R3): check two rows by keyboard, Cancel… with a reason, and the one bulk request goes only after the window.
  { name: "ng-board-select-bulk", widths: [1280], keyboard: true, mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "space-checks-a-row", run: async (p) => { await p.getByRole("button", { name: /^Bump the VS Code/ }).focus(); await p.keyboard.press("Space"); await expect(p.getByRole("checkbox", { name: /^Select Bump the VS Code/ })).toBeChecked(); await expect(p.getByText("1 selected")).toBeVisible(); } },
    { name: "arrow-and-space-checks-another", run: async (p) => { await p.keyboard.press("ArrowDown"); await p.keyboard.press("Space"); await expect(p.getByText("2 selected")).toBeVisible(); } },
    { name: "cancel-asks", run: async (p) => { await p.getByRole("button", { name: "Cancel 2…" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: /Reason/ })).toBeFocused(); await expect(p.getByRole("button", { name: "Cancel 2", exact: true })).toBeDisabled(); } },
    { name: "reason", run: async (p) => { await p.keyboard.type("Superseded by kraft-cb61"); } },
    { name: "confirm-holds-the-send", run: async (p) => {
      const sent: { at: number; body: unknown }[] = [];
      p.on("request", (r) => { if (r.method() === "POST" && /\/work-items\/(bulk|[^/]+\/(cancel|abandon))$/.test(r.url())) sent.push({ at: Date.now(), body: r.postDataJSON() }); });
      const t0 = Date.now();
      await p.keyboard.press("Tab"); await p.keyboard.press("Enter");
      await expect(p.getByText("Cancelling 2 items…")).toBeVisible();
      await p.waitForTimeout(4000);
      expect(sent).toEqual([]);
      await expect.poll(() => sent.length, { timeout: 4000 }).toBe(1);
      expect(sent[0].at - t0).toBeGreaterThanOrEqual(4900);
      expect(sent[0].body).toEqual({ action: "cancel", ids: expect.any(Array), reason: "Superseded by kraft-cb61" });
      expect((sent[0].body as { ids: string[] }).ids).toHaveLength(2);
    }, wait: 600 },
  ] },
  // ux2-W6 E: the peek by keyboard, its width kept across a reload, and ⌘/Ctrl-Enter to the item page.
  { name: "ng-peek-open", widths: [1280], keyboard: true, mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "arrow-to-a-row", run: async (p) => { await p.getByRole("button", { name: /^Design the caching layer/ }).focus(); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("button", { name: /^Fix flaky retry test/ })).toBeFocused(); } },
    { name: "enter-opens-the-peek", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("complementary", { name: "kraft-7d21 pane" })).toBeVisible(); await expect(p).toHaveURL(/[?&]sel=/); } },
    { name: "keys-widen-it", run: async (p) => {
      const h = p.getByRole("separator", { name: "Resize pane" });
      await h.focus();
      const w0 = Number(await h.getAttribute("aria-valuenow"));
      for (let i = 0; i < 4; i++) await p.keyboard.press("ArrowLeft");
      await expect(h).toHaveAttribute("aria-valuenow", String(w0 + 64));
    } },
    { name: "reload-keeps-the-width", run: async (p) => {
      const w = await p.getByRole("separator", { name: "Resize pane" }).getAttribute("aria-valuenow");
      await p.reload(); await p.locator("main h1").first().waitFor();
      await expect(p.getByRole("separator", { name: "Resize pane" })).toHaveAttribute("aria-valuenow", w!);
    }, wait: 700 },
    { name: "ctrl-enter-opens-the-item", run: async (p) => { await p.getByRole("button", { name: /^Fix flaky retry test/ }).focus(); await p.keyboard.press("Control+Enter"); await expect(p).toHaveURL(/\/ng\/work-items\/[0-9a-f]+$/); } },
  ] },
  // ux2-W6 F: the composer from the header button; a spec by its path; ⌘↵ sends the dry run, then the create with autostart.
  { name: "ng-composer-create", widths: [1280], mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "open", run: async (p) => { await p.getByRole("button", { name: "+ New work item" }).click(); await expect(p.getByRole("textbox", { name: "Title" })).toBeFocused(); await p.getByText(/of 15 nodes run/).waitFor(); } },
    { name: "title", run: async (p) => { await p.keyboard.type("Design the caching layer for document search"); } },
    { name: "attach-spec", run: async (p) => {
      await p.getByRole("button", { name: "+ spec" }).click();
      await expect(p.getByRole("textbox", { name: /Search specs/ })).toBeFocused();
      await p.keyboard.type("docs/specs/doc-search-cache.md"); await p.waitForTimeout(300); await p.keyboard.press("Enter");
      await expect(p.getByText("13 of 15 nodes run · 4 gates")).toBeVisible();
    } },
    { name: "cmd-enter-creates-and-starts", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items$/.test(new URL(r.url()).pathname) && !r.url().includes("dry_run"));
      await p.getByRole("textbox", { name: "Title" }).focus();
      await p.keyboard.press("Control+Enter");
      const r = await sent;
      expect(r.postDataJSON()).toMatchObject({ title: "Design the caching layer for document search", autostart: true, attachments: [{ kind: "spec", path: "docs/specs/doc-search-cache.md" }] });
      await expect(p.getByRole("region", { name: "New work item" })).toHaveCount(0);
      await expect(p).toHaveURL(/[?&]sel=/);
    }, wait: 700 },
  ] },
];
