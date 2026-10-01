import { expect } from "@playwright/test";
import { ngItem, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // ux2-W8 exit: a request-changes review, from the item page's diff line, sends the threads and starts the fix round on the right node; the item page shows it.
  { name: "ng-review-request-changes", widths: [1280], keyboard: true, start: ngItem("needs-gate"), steps: [
    { name: "open-review", run: async (p) => {
      await p.getByRole("link", { name: "Review changes" }).focus(); await p.keyboard.press("Enter");
      await expect(p.getByRole("heading", { name: /^Review changes:/ })).toBeAttached();
      await expect(p.getByRole("group", { name: /^Lines of / }).first()).toBeVisible();
    }, kbd: true },
    { name: "pick-a-line", run: async (p) => {
      await p.getByRole("group", { name: /^Lines of / }).first().focus();
      for (let i = 0; i < 3; i++) await p.keyboard.press("ArrowDown");
      await p.keyboard.press("Enter");
      await expect(p.getByRole("textbox", { name: "Comment" })).toBeFocused();
    }, kbd: true },
    { name: "must-fix-add", run: async (p) => {
      await p.keyboard.type("Keep the old signature until the callers move.");
      await p.getByRole("radio", { name: "Must fix" }).focus(); await p.keyboard.press("Space");
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items\/[^/]+\/threads$/.test(r.url()));
      await p.getByRole("button", { name: "Add to review" }).focus(); await p.keyboard.press("Enter");
      expect((await sent).postDataJSON()).toMatchObject({ label: "must_fix", side: "new", start_line: 2, end_line: 2 });
      await expect(p.getByText("Keep the old signature until the callers move.")).toBeVisible();
    }, kbd: true },
    { name: "finish", run: async (p) => {
      await p.getByRole("button", { name: "Request changes" }).focus(); await p.keyboard.press("Enter");
      await expect(p.getByRole("dialog", { name: "Finish your review" })).toBeVisible();
      await expect(p.getByRole("radio", { name: /Request changes/ })).toBeChecked();
    }, kbd: true },
    { name: "submit", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/review$/.test(r.url()));
      await p.getByRole("button", { name: "Submit review" }).focus(); await p.keyboard.press("Enter");
      const r = await sent;
      expect(new URL(r.url()).pathname).toMatch(/\/gates\/final_review\/review$/);
      expect(r.postDataJSON()).toEqual({ outcome: "request_changes" });
      await expect.poll(() => new URL(p.url()).search).toBe("?sel=implementation");
      await expect(p.getByText("RUNNING", { exact: true })).toBeVisible();
    }, wait: 700 },
  ] },
];
