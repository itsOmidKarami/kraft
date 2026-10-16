/**
 * The demo's one addition to the page: a corner badge that says the data is sample data
 * and nothing is saved, and links to the install guide. Outside the React tree, so the
 * app renders exactly as it ships; the × hides it for the rest of the visit.
 */
const HIDDEN = "kraft.demo.badge.hidden";

const CSS = `
.kd-badge { position: fixed; z-index: 2147483000; right: 16px; bottom: 16px; display: flex; align-items: center; gap: 10px;
  max-width: calc(100vw - 32px); box-sizing: border-box; padding: 6px 6px 6px 12px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--surface); color: var(--text-sub); font: 12px/1.4 var(--font-sans, system-ui, sans-serif); box-shadow: 0 6px 24px rgb(0 0 0 / 0.28); }
.kd-badge b { color: var(--warn); font-weight: 600; letter-spacing: 0.06em; }
.kd-badge a { color: var(--text); text-underline-offset: 3px; white-space: nowrap; }
.kd-badge button { display: grid; place-items: center; width: 24px; height: 24px; flex: none; border: 0; border-radius: 999px;
  background: transparent; color: var(--text-muted); font-size: 16px; line-height: 1; cursor: pointer; }
.kd-badge button:hover { background: var(--surface-2); color: var(--text); }
.kd-badge :focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
@media (max-width: 767px) { .kd-badge { right: 8px; bottom: calc(72px + env(safe-area-inset-bottom)); } }
`;

const hidden = () => {
  try { return sessionStorage.getItem(HIDDEN) === "1"; } catch { return false; }
};

export function showBadge(installUrl: string) {
  if (hidden()) return;
  const style = document.createElement("style");
  style.textContent = CSS;
  const badge = document.createElement("aside");
  badge.className = "kd-badge";
  badge.setAttribute("aria-label", "About this demo");
  badge.innerHTML = `<span><b>DEMO</b> · sample data, nothing is saved</span><a href="${installUrl}">Install Kraft</a><button type="button" aria-label="Hide this notice" data-tip="Hide this notice">×</button>`;
  badge.querySelector("button")!.addEventListener("click", () => {
    badge.remove();
    try { sessionStorage.setItem(HIDDEN, "1"); } catch { /* a private window: it comes back on the next load */ }
  });
  document.head.append(style);
  document.body.append(badge);
}
