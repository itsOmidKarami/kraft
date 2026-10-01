import { expect, type Page } from "@playwright/test";
import { ng, type Flow } from "../flowKit";

/** With the pane docked, no lane runs under it, the canvas needs no sideways scroll, and every part of a lane's head sits inside the lane. */
const fits = async (p: Page) => {
  await p.locator(".hn-lane").first().waitFor();
  const m = await p.evaluate(() => {
    const pane = document.querySelector("aside")!.getBoundingClientRect();
    const canvas = document.querySelector(".hn-canvas") as HTMLElement;
    const lanes = [...document.querySelectorAll(".hn-lane")].map((l) => {
      const r = l.getBoundingClientRect();
      const out = [...l.querySelectorAll(".hn-lane-head > *, .hn-glyph, .hn-more")].filter((e) => e.getBoundingClientRect().right > r.right + 0.5).map((e) => e.className);
      return { right: r.right, out };
    });
    return { pane: pane.left, scroll: canvas.scrollWidth - canvas.clientWidth, lanes };
  });
  expect(m.scroll).toBeLessThanOrEqual(1);
  for (const l of m.lanes) { expect(l.right).toBeLessThanOrEqual(m.pane + 0.5); expect(l.out).toEqual([]); }
};

export const flows: Flow[] = [
  // Kraft-9d8b2.17: the lanes reserve the docked pane's width, on the floor, a harness and a profile.
  { name: "ng-harness-lanes-fit-the-pane", widths: [1280], mock: { harnesses: "floor" }, start: ng("/ng/templates/harnesses"), steps: [
    { name: "floor", run: fits },
    { name: "harness", run: async (p) => { await p.goto("/ng/templates/harnesses?harness=claude"); await fits(p); } },
    { name: "profile", run: async (p) => { await p.goto("/ng/templates/harnesses?profile=strong"); await fits(p); } },
  ] },
];
