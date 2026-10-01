import { expect } from "@playwright/test";
import { ng, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // ux2-W14 F: a harness set to Never while a task selects it is a problem that names the task, on the list, the lane and the header; setting it back clears it.
  { name: "ng-harness-never-blocks-publish", widths: [1280], keyboard: true, mock: { harnesses: "floor" }, start: ng("/ng/templates/harnesses?harness=claude"), steps: [
    { name: "no-problems", run: async (p) => { await expect(p.getByText("PROBLEM")).toHaveCount(0); } },
    { name: "set-never", run: async (p) => { await p.getByRole("radio", { name: "Never" }).click(); await expect(p.getByText(/\d+ PROBLEMS?/)).toBeVisible(); } },
    { name: "names-the-task", run: async (p) => {
      await expect(p.getByRole("complementary", { name: "claude pane" })).toContainText("implementation.main.implementer: harness 'claude' is not in its allowed_harnesses");
      await expect(p.getByRole("navigation", { name: "Harnesses and profiles" }).getByRole("button", { name: "claude, Never, has a problem" })).toBeVisible();
    } },
    { name: "publish-blocked", run: async (p) => {
      await p.getByRole("button", { name: "Review & publish" }).click();
      await expect(p.getByRole("button", { name: "Publish", exact: true })).toBeDisabled();
      await expect(p.getByRole("complementary", { name: /^Draft · .* pane$/ })).toContainText("implementation.main.implementer: harness 'claude' is not in its allowed_harnesses");
      await p.keyboard.press("Escape");
    } },
    { name: "set-back", run: async (p) => { await p.getByRole("radio", { name: "Available" }).click(); await expect(p.getByText(/\d+ PROBLEMS?/)).toHaveCount(0); } },
    { name: "publish", run: async (p) => {
      await p.getByRole("navigation", { name: "Harnesses and profiles" }).getByRole("button", { name: /^cursor, Never/ }).click();
      await p.getByRole("radio", { name: "Available" }).click();
      await p.getByRole("button", { name: "Review & publish" }).click();
      const sent = p.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/drafts/harnesses/harnesses/publish"));
      await p.getByRole("button", { name: "Publish", exact: true }).click();
      await sent;
      await expect(p.getByText("Published harnesses · new launches use it from now on")).toBeVisible();
    } },
  ] },
];
