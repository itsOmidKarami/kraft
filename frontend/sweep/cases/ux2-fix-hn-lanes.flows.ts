import { expect, type Page } from "@playwright/test";
import { ng, type Flow } from "../flowKit";

/** With the pane docked, the canvas ends at the pane's edge, and scrolled to its end it shows every lane whole, each part of a lane inside it. */
const fits = async (p: Page) => {
  await p.locator(".hn-lane").first().waitFor();
  await p.evaluate(() => { const c = document.querySelector(".hn-canvas") as HTMLElement; c.scrollLeft = c.scrollWidth; });
  const m = await p.evaluate(() => {
    const pane = [...document.querySelectorAll("aside")].find((a) => !a.classList.contains("ng-sidebar"))!.getBoundingClientRect();
    const canvas = (document.querySelector(".hn-canvas") as HTMLElement).getBoundingClientRect();
    const lanes = [...document.querySelectorAll(".hn-lane")].map((l) => {
      const r = l.getBoundingClientRect();
      const out = [...l.querySelectorAll(".hn-lane-head > *, .hn-glyph, .hn-more")].filter((e) => e.getBoundingClientRect().right > r.right + 0.5).map((e) => e.className);
      return { right: r.right, out };
    });
    return { pane: pane.left, canvas: canvas.right, lanes };
  });
  expect(m.canvas).toBeLessThanOrEqual(m.pane + 0.5);
  for (const l of m.lanes) { expect(l.right).toBeLessThanOrEqual(m.canvas + 0.5); expect(l.out).toEqual([]); }
};

export const flows: Flow[] = [
  // Kraft-9d8b2.17: the canvas ends at the docked pane's edge and scrolls to every lane's right end, on the floor, a harness and a profile.
  { name: "ng-harness-lanes-fit-the-pane", widths: [1280], mock: { harnesses: "floor" }, start: ng("/ng/templates/harnesses"), steps: [
    { name: "floor", run: fits },
    { name: "harness", run: async (p) => { await p.goto("/ng/templates/harnesses?harness=claude"); await fits(p); } },
    { name: "profile", run: async (p) => { await p.goto("/ng/templates/harnesses?profile=strong"); await fits(p); } },
  ] },
];
