import { withBase } from "../src/ng/base";
import { PHONE_QUERY } from "../src/ng/phone/usePhone";
import { findAllByRole, ringWhenFound } from "./ring";
import { TRIES, type Look, type Spot, type Tries, type TryId } from "./tries";

/**
 * The demo's one addition to the page: a corner badge that says the data is sample data
 * and nothing is saved, links to the install guide, and opens a list of things to try.
 * Bottom left of the page on a desktop, past the sidebar and lifted over a bottom bar as the
 * toasts are (`--toast-bottom`; not under the gate review, which covers the bar), clear of the
 * actions kept bottom right (the review page's Request changes and Approve); above the bottom
 * navigation on a phone. Only its cards take clicks: the box around them lets them through to
 * the page.
 * Outside the React tree, so the app renders exactly as it ships; the × hides it all for
 * the rest of the visit. The list opens by itself once, on a first visit on a desktop;
 * after that it stays as the visitor last left it.
 */
const HIDDEN = "kraft.demo.badge.hidden";
const OPEN = "kraft.demo.tries";

const CSS = `
.kd-badge { --kd-bottom: var(--toast-bottom, 16px); position: fixed; z-index: 2147483000; left: calc(var(--ng-side-w, 0px) + 16px); bottom: var(--kd-bottom);
  display: flex; flex-direction: column; align-items: flex-start; gap: 8px; max-width: calc(100vw - var(--ng-side-w, 0px) - 32px); box-sizing: border-box;
  color: var(--text-sub); font: 12px/1.4 var(--font-sans, system-ui, sans-serif); pointer-events: none; }
/* The gate review covers the review page's bottom bar; its only bottom action is right, under the toasts, so they stay lifted and the badge does not. */
body:has(.rv-gate) .kd-badge { --kd-bottom: 16px; }
.kd-badge > * { pointer-events: auto; }
.kd-badge * { box-sizing: border-box; }
.kd-pill { display: flex; align-items: center; gap: 10px; max-width: 100%; padding: 6px 6px 6px 12px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--surface); box-shadow: 0 6px 24px rgb(0 0 0 / 0.28); }
.kd-pill b { color: var(--warn); font-weight: 600; letter-spacing: 0.06em; }
.kd-badge a { color: var(--text); text-underline-offset: 3px; white-space: nowrap; }
.kd-badge button { font: inherit; cursor: pointer; }
.kd-badge .kd-x { display: grid; place-items: center; width: 24px; height: 24px; flex: none; border: 0; border-radius: 999px;
  background: transparent; color: var(--text-muted); font-size: 16px; line-height: 1; }
.kd-badge .kd-x:hover { background: var(--surface-2); color: var(--text); }
.kd-toggle { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--surface-2); color: var(--text); white-space: nowrap; }
.kd-toggle:hover { border-color: var(--text-muted); }
.kd-toggle .kd-count { color: var(--text-muted); font-variant-numeric: tabular-nums; }
.kd-toggle .kd-chev { color: var(--text-muted); transition: transform 120ms; }
.kd-toggle[aria-expanded="true"] .kd-chev { transform: rotate(180deg); }
.kd-tries { width: 340px; max-width: 100%; max-height: calc(100vh - var(--kd-bottom) - 64px); overflow: auto; padding: 14px 14px 10px; border: 1px solid var(--border); border-radius: 12px;
  background: var(--surface); box-shadow: 0 10px 32px rgb(0 0 0 / 0.32); }
/* Every hidden part stays hidden, whatever display a layout gives it (the phone's Show me is a flex box). */
.kd-badge [hidden] { display: none !important; }
.kd-tries h2 { margin: 0 0 4px; color: var(--text); font-size: 13px; font-weight: 600; }
.kd-tries p { margin: 0 0 10px; }
.kd-tries ol { margin: 0; padding: 0; list-style: none; }
.kd-try { display: grid; grid-template-columns: 18px 1fr auto; align-items: start; gap: 2px 10px; padding: 8px 0; border-top: 1px solid var(--line); }
.kd-tick { width: 16px; height: 16px; margin-top: 1px; border: 1.5px solid var(--stroke); border-radius: 999px; display: grid; place-items: center; color: var(--bg); font-size: 10px; line-height: 1; }
.kd-try.is-done .kd-tick { border-color: var(--ok); background: var(--ok); }
.kd-try-title { color: var(--text); font-weight: 500; }
.kd-try.is-done .kd-try-title { color: var(--text-sub); }
.kd-try-detail { grid-column: 2; color: var(--text-muted); }
.kd-try-go { grid-column: 3; grid-row: 1; align-self: center; }
.kd-try-none { grid-column: 2 / 4; color: var(--text-muted); font-style: italic; }
.kd-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.kd-badge :focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.kd-ring { position: fixed; z-index: 2147482999; pointer-events: none; border: 2px solid var(--accent, var(--focus)); border-radius: 8px;
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent, var(--focus)) 30%, transparent); animation: kd-pulse 1.2s ease-out infinite; }
@keyframes kd-pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent, var(--focus)) 55%, transparent); } 100% { box-shadow: 0 0 0 10px transparent; } }
@media (prefers-reduced-motion: reduce) { .kd-ring { animation: none; } .kd-toggle .kd-chev { transition: none; } }
@media (max-width: 767px) {
  .kd-badge { right: 8px; left: 8px; max-width: none; align-items: flex-end; bottom: calc(72px + env(safe-area-inset-bottom)); }
  .kd-tries { width: 100%; max-height: calc(100vh - 180px - env(safe-area-inset-bottom)); }
  .kd-pill .kd-note { display: none; }
  /* The phone's tap targets are 44px, as the app's are. */
  .kd-pill { gap: 4px; padding: 2px 2px 2px 12px; }
  .kd-toggle { height: 44px; padding: 0 12px; }
  .kd-badge .kd-x { width: 44px; height: 44px; }
  .kd-pill a { display: inline-flex; align-items: center; min-height: 44px; padding: 0 6px; }
  .kd-try-go { grid-row: 1 / 3; display: inline-flex; align-items: center; min-height: 44px; padding: 0 6px; }
}
@media (min-width: 768px) { .kd-tries .kd-phone { display: none; } }
`;

const hidden = () => {
  try { return sessionStorage.getItem(HIDDEN) === "1"; } catch { return false; }
};

const phone = () => typeof matchMedia === "function" && matchMedia(PHONE_QUERY).matches;

/**
 * Whether the list starts open. A first visit opens it on a desktop and keeps it shut on a phone,
 * and is remembered as shut, so it opens by itself only once; a toggle remembers what it was left as.
 * With no storage (a private window) every load is a first visit.
 */
export function startsOpen(isPhone: boolean): boolean {
  try {
    const was = localStorage.getItem(OPEN);
    if (was === "open" || was === "closed") return was === "open";
    localStorage.setItem(OPEN, "closed");
  } catch { /* no storage: nothing to remember it in */ }
  return !isPhone;
}

const remember = (open: boolean) => {
  try { localStorage.setItem(OPEN, open ? "open" : "closed"); } catch { /* as above */ }
};

/**
 * A Show me's click: the in-app address by the router (pushState, then the popstate BrowserRouter
 * follows), not by loading the page, which would start the demo over. A click something else already
 * took (the item page's leave guard, for an unapplied chain draft) or one asking for a new tab is left to it.
 */
export function showMe(e: MouseEvent, a: HTMLAnchorElement, spot: Spot | undefined, skip: Element): (() => void) | null {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return null;
  e.preventDefault();
  const win = a.ownerDocument.defaultView!;
  const url = new URL(a.href);
  const to = url.pathname + url.search;
  // What the page being left shows that matches: it may still be drawn when the first look comes.
  const stale = new Set<Element>();
  if (to !== win.location.pathname + win.location.search) {
    if (spot) for (const el of findAllByRole(a.ownerDocument, spot.role, spot.name, skip)) stale.add(el);
    // The router's own entry shape, so its index (and Back) stays right.
    const idx = (win.history.state?.idx ?? 0) + 1;
    win.history.pushState({ usr: null, key: Math.random().toString(36).slice(2, 10), idx }, "", to);
    win.dispatchEvent(new PopStateEvent("popstate", { state: win.history.state }));
  }
  // Not read yet (a click before the first look answered): the address it holds, the board, and no ring.
  return spot ? ringWhenFound(a.ownerDocument, spot.role, spot.name, skip, stale) : null;
}

export function showBadge(installUrl: string, tries: Tries, look: Look, doc: Document = document) {
  if (hidden()) return;
  const style = doc.createElement("style");
  style.textContent = CSS;
  const badge = doc.createElement("aside");
  badge.className = "kd-badge";
  badge.setAttribute("aria-label", "About this demo");
  badge.innerHTML = `
    <section class="kd-tries" id="kd-tries" aria-labelledby="kd-tries-head" hidden>
      <h2 id="kd-tries-head">Things to try</h2>
      <p><span class="kd-phone">Sample data, and nothing is saved. </span>The board sorts every work item by what it needs from you. Five more things to try:</p>
      <ol>${TRIES.map((t) => `
        <li class="kd-try" data-try="${t.id}">
          <span class="kd-tick" aria-hidden="true"></span>
          <span class="kd-try-title">${t.title}<span class="kd-sr kd-state"></span></span>
          <span class="kd-try-detail">${t.detail}</span>
          <a class="kd-try-go" href="${withBase("/")}" aria-label="Show me: ${t.title}">Show me</a>
          <span class="kd-try-none" hidden>${t.none}</span>
        </li>`).join("")}
      </ol>
    </section>
    <p class="kd-sr" role="status"></p>
    <div class="kd-pill">
      <span><b>DEMO</b><span class="kd-note"> · sample data, nothing is saved</span></span>
      <button type="button" class="kd-toggle" aria-expanded="false" aria-controls="kd-tries">Things to try <span class="kd-count"></span><span class="kd-chev" aria-hidden="true">▴</span></button>
      <a href="${installUrl}">Install Kraft</a>
      <button type="button" class="kd-x" aria-label="Hide this notice" data-tip="Hide this notice">×</button>
    </div>`;
  const list = badge.querySelector<HTMLElement>(".kd-tries")!;
  const toggle = badge.querySelector<HTMLButtonElement>(".kd-toggle")!;
  const count = badge.querySelector<HTMLElement>(".kd-count")!;
  const status = badge.querySelector<HTMLElement>("[role=status]")!;
  const said = new Set<TryId>(tries.done);
  const spots = new Map<TryId, Spot | null>();
  let cancelRing: (() => void) | null = null;

  const draw = () => {
    const n = tries.done.size;
    count.textContent = `${n}/${TRIES.length}`;
    toggle.setAttribute("aria-label", `Things to try, ${n} of ${TRIES.length} done`);
    for (const t of TRIES) {
      const li = badge.querySelector<HTMLElement>(`[data-try="${t.id}"]`)!;
      const done = tries.done.has(t.id);
      li.classList.toggle("is-done", done);
      li.querySelector(".kd-tick")!.textContent = done ? "✓" : "";
      li.querySelector(".kd-state")!.textContent = done ? " (done)" : "";
      const spot = spots.get(t.id);
      const go = li.querySelector<HTMLAnchorElement>(".kd-try-go")!;
      // Unknown yet (the first look has not answered): keep the link. Known to be nowhere: say so instead.
      go.hidden = spot === null;
      li.querySelector<HTMLElement>(".kd-try-none")!.hidden = spot !== null;
      if (spot) go.href = withBase(spot.path);
    }
  };

  // Where each Show me goes depends on the data (which item waits at a gate now), so it is read again
  // whenever the list opens or a request may have moved something.
  let reading: Promise<void> | null = null;
  let again = false;
  const refresh = () => {
    if (reading) { again = true; return; }
    reading = Promise.all(TRIES.map(async (t) => spots.set(t.id, await t.where(look).catch(() => null)))).then(() => {
      reading = null;
      draw();
      if (again) { again = false; refresh(); }
    });
  };

  const setOpen = (open: boolean) => {
    list.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) refresh();
  };

  toggle.addEventListener("click", () => {
    const open = list.hidden !== false;
    setOpen(open);
    remember(open);
  });
  // Escape shuts the list only from inside it, and goes no further: the app's own Escape (a peek, a
  // dialog) never hears it. Focus was in the list, which is gone, so it goes back to the toggle.
  badge.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || list.hidden || !list.contains(e.target as Node)) return;
    e.preventDefault();
    e.stopPropagation();
    setOpen(false);
    remember(false);
    toggle.focus();
  });
  for (const t of TRIES) {
    const go = badge.querySelector<HTMLAnchorElement>(`[data-try="${t.id}"] .kd-try-go`)!;
    go.addEventListener("click", (e) => {
      const taken = e.defaultPrevented;
      const off = showMe(e, go, spots.get(t.id) ?? undefined, badge);
      if (off) { cancelRing?.(); cancelRing = off; }
      // A phone's list covers the page it just went to: it shuts, not remembered so, and the toggle has focus in place of the link.
      if (!taken && e.defaultPrevented && phone()) {
        setOpen(false);
        toggle.focus();
      }
    });
  }
  // A tick is said once, politely: "Done: Approve a gate. 2 of 5."
  const announce = () => {
    const fresh = TRIES.filter((t) => tries.done.has(t.id) && !said.has(t.id));
    if (!fresh.length) return;
    for (const t of fresh) said.add(t.id);
    status.textContent = `Done: ${fresh.map((t) => t.title).join(", ")}. ${tries.done.size} of ${TRIES.length}.`;
  };
  const unlisten = tries.onChange((moved) => {
    draw();
    announce();
    if (moved && !list.hidden) refresh();
  });
  badge.querySelector<HTMLButtonElement>(".kd-x")!.addEventListener("click", () => {
    cancelRing?.();
    unlisten();
    badge.remove();
    try { sessionStorage.setItem(HIDDEN, "1"); } catch { /* a private window: it comes back on the next load */ }
  });
  list.addEventListener("pointerenter", refresh);
  list.addEventListener("focusin", refresh);

  draw();
  setOpen(startsOpen(phone()));
  doc.head.append(style);
  doc.body.append(badge);
  return badge;
}
