import { expect } from "@playwright/test";
import { ngItem, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // Keyboard: Raise cap opens the editor with the field focused; a value above the current one enables Save & retry; Esc closes and returns focus to the button.
  { name: "ng-raise-cap-keyboard", widths: [1280], keyboard: true, start: async (p, S) => {
    (S.bundles[S.ng.capped].item.stop as { limit?: object }).limit = { path: "verification", key: "max_attempts", value: 3, maximum: 5 };
    await ngItem("capped")(p, S);
  }, steps: [
    { name: "open", run: async (p) => { await p.getByRole("button", { name: "Raise cap" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("dialog")).toBeVisible(); await expect(p.getByRole("spinbutton")).toBeFocused(); }, kbd: true },
    { name: "type-then-save-enabled", run: async (p) => { await expect(p.getByRole("button", { name: "Save & retry" })).toBeDisabled(); await p.getByRole("spinbutton").fill("4"); await expect(p.getByRole("button", { name: "Save & retry" })).toBeEnabled(); }, kbd: true },
    { name: "escape-returns-focus", run: async (p) => { await p.keyboard.press("Escape"); await expect(p.getByRole("dialog")).toHaveCount(0); await expect(p.getByRole("button", { name: "Raise cap" })).toBeFocused(); }, kbd: true },
    { name: "save-patches-and-retries", run: async (p) => {
      const sent: string[] = [];
      p.on("request", (r) => { if (r.method() !== "GET" && r.url().includes("/api/work-items/")) sent.push(`${r.method()} ${new URL(r.url()).pathname}`); });
      await p.getByRole("button", { name: "Raise cap" }).click(); await p.getByRole("spinbutton").fill("4"); await p.getByRole("button", { name: "Save & retry" }).click();
      await expect(p.getByRole("dialog")).toHaveCount(0);
      expect(sent.filter((s) => /\/api\/work-items\/[^/]+(\/retry)?$/.test(s.split(" ")[1]))).toEqual([expect.stringMatching(/^PATCH /), expect.stringMatching(/^POST .*\/retry$/)]);
    } },
  ] },
];
