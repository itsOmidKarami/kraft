import { expect, test } from "@playwright/test";
import { STATES } from "./screens";

/**
 * The icon-only audit. On every screen in screens.ts, each visible button, link or summary whose text has no
 * letter or digit must carry an accessible name (aria-label or title) and a tooltip (`data-tip`): a name for a
 * screen reader, a tip for anyone else. That a tip really shows on hover and on keyboard focus is checked by
 * the "tooltips:" rows of shell.spec.ts; hovering or focusing every control here would only be flaky (a menu
 * opens on hover, a dialog traps focus).
 */
const SIZES = { default: [1280, 800], phone: [390, 844] } as const;

/** Screens with no icon-only control at all. Any other that finds none did not render, and must not pass for it. */
const NO_ICONS = new Set(["sign-in/idle", "sign-in/error", "sign-in/locked", "phone/node", "phone/gate-node", "phone/gate-review", "phone/new-item", "phone/search", "phone/analytics", "phone/more", "phone/chains", "phone/policy", "phone/sign-in", "phone/node-yaml"]);

interface Hit { label: string | null; tip: string | null; title: string | null; html: string }

for (const st of STATES) {
  test(`icons ${st.area}/${st.state}`, async ({ page }) => {
    const [width, height] = SIZES[st.viewport ?? "default"];
    await page.setViewportSize({ width, height });
    await st.app(page);
    const hits: Hit[] = await page.evaluate(() => {
      const text = (n: Node): string => n instanceof Text ? n.data : !(n instanceof HTMLElement) || n.getAttribute("aria-hidden") === "true" || /sr-only|visually-hidden/.test(n.className.toString()) ? "" : [...n.childNodes].map(text).join("");
      const out: Hit[] = [];
      document.querySelectorAll<HTMLElement>('button, [role="button"], a[href], summary').forEach((b) => {
        const role = b.getAttribute("role");
        if (role && role !== "button") return;
        const r = b.getBoundingClientRect();
        if (r.width < 2 || r.height < 2 || getComputedStyle(b).visibility === "hidden" || b.closest('[aria-hidden="true"]')) return;
        if (/[\p{L}\p{N}]/u.test(text(b)) || /[\p{L}\p{N}]/u.test(b.innerText)) return;
        out.push({ label: b.getAttribute("aria-label") ?? (b.getAttribute("aria-labelledby") ? "(labelledby)" : null), tip: b.getAttribute("data-tip"), title: b.getAttribute("title"), html: b.outerHTML.slice(0, 140) });
      });
      return out;
    });
    if (!NO_ICONS.has(`${st.area}/${st.state}`)) expect(hits.length, `${st.area}/${st.state} has icon-only controls to audit (else it did not render)`).toBeGreaterThan(0);
    const problems = hits.flatMap((h) => [!(h.label || h.title) && "no accessible name", !h.tip && "no tooltip"].filter(Boolean).map((why) => `${why}: ${h.html}`));
    expect(problems, `icon-only controls on ${st.area}/${st.state} without a name or a tooltip`).toEqual([]);
  });
}
