import { expect } from "@playwright/test";
import { ng, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // Kraft-9d8b2.50: after a click on a line number, Enter and then c open the composer on that line.
  { name: "ng-review-number-click-then-enter", widths: [1280], start: async (p, S) => ng(`/ng/work-items/${S.ng["needs-gate"]}/review`)(p), steps: [
    { name: "click-the-number", run: async (p) => {
      await p.getByRole("button", { name: "Pick new line 5", exact: true }).first().click();
      await expect(p.getByRole("group", { name: /^Lines of / }).first()).toBeFocused();
    } },
    { name: "enter-opens-the-composer", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: "Comment" })).toBeFocused(); }, kbd: true },
    { name: "cancel-then-c-opens-it-again", run: async (p) => {
      await p.getByRole("button", { name: "Cancel" }).click();
      await p.getByRole("button", { name: "Pick new line 5", exact: true }).first().click();
      await p.keyboard.press("c");
      await expect(p.getByRole("textbox", { name: "Comment" })).toBeFocused();
    }, kbd: true },
  ] },
];
