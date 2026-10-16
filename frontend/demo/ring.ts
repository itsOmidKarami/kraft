/**
 * Finding a control the way a person (and Playwright's getByRole) names it, and ringing it: what a
 * things-to-try "Show me" does once it has navigated. Plain DOM, outside the React tree.
 */

/** The role an element has: its own `role`, or the one its tag implies, for the few kinds a Show me rings. */
function roleOf(el: Element): string | null {
  const own = el.getAttribute("role")?.trim().split(/\s+/)[0];
  if (own) return own;
  const tag = el.tagName.toLowerCase();
  if (tag === "button") return "button";
  if (tag === "a" && el.hasAttribute("href")) return "link";
  if (tag === "textarea") return "textbox";
  if (tag === "input") {
    const type = (el.getAttribute("type") ?? "text").toLowerCase();
    if (["button", "submit", "reset"].includes(type)) return "button";
    if (type === "search") return "searchbox";
    if (["text", "email", "url", "tel"].includes(type)) return "textbox";
  }
  return null;
}

/** The text a subtree reads as: aria-hidden parts left out, a labelled child read by its label. */
function textOf(n: Node): string {
  if (n.nodeType === Node.TEXT_NODE) return n.textContent ?? "";
  if (!(n instanceof Element) || n.getAttribute("aria-hidden") === "true" || (n as HTMLElement).hidden) return "";
  const label = n.getAttribute("aria-label")?.trim();
  if (label) return ` ${label} `;
  return [...n.childNodes].map(textOf).join("");
}

/** An element's accessible name, by the rules its controls use here: labelledby, aria-label, its label, its text. */
export function nameOf(el: Element): string {
  const squash = (s: string) => s.replace(/\s+/g, " ").trim();
  const by = el.getAttribute("aria-labelledby");
  if (by) return squash(by.split(/\s+/).map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "").join(" "));
  const label = el.getAttribute("aria-label");
  if (label?.trim()) return squash(label);
  const labels = (el as HTMLInputElement).labels;
  if (labels?.length) return squash([...labels].map((l) => l.textContent ?? "").join(" "));
  return squash([...el.childNodes].map(textOf).join(""));
}

/** Laid out and not hidden: a box of its own. The phone layout draws other pages, and a control it lacks has none. */
function laidOut(el: Element): boolean {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && el.ownerDocument.defaultView!.getComputedStyle(el).visibility !== "hidden";
}

/** At least partly inside the window, where a ring can be seen. */
function inView(el: Element): boolean {
  const r = el.getBoundingClientRect();
  const win = el.ownerDocument.defaultView!;
  return r.bottom > 0 && r.right > 0 && r.top < win.innerHeight && r.left < win.innerWidth;
}

/** The first laid-out control with this role and name, outside `skip` (the list's own controls). */
export function findByRole(doc: Document, role: string, name: string | RegExp, skip?: Element | null): HTMLElement | null {
  for (const el of doc.querySelectorAll<HTMLElement>("button, a[href], textarea, input, [role]")) {
    if (skip?.contains(el) || roleOf(el) !== role) continue;
    const n = nameOf(el);
    if ((typeof name === "string" ? n === name : name.test(n)) && laidOut(el)) return el;
  }
  return null;
}

/** How long a ring stays, and how long a Show me waits for its target to be drawn. */
export const RING_MS = 6000;
export const WAIT_MS = 4000;

/**
 * A ring around `target`, kept on it frame by frame (so it follows scroll, resize and the page moving
 * under it), hidden while the target is scrolled out of the window, until RING_MS pass, the target
 * goes, or the person presses anywhere. Returns its remover.
 */
export function ring(target: HTMLElement): () => void {
  const doc = target.ownerDocument;
  const win = doc.defaultView!;
  const el = doc.createElement("div");
  el.className = "kd-ring";
  el.setAttribute("aria-hidden", "true");
  doc.body.append(el);
  let frame = 0;
  const place = () => {
    if (!target.isConnected || !laidOut(target)) return stop();
    el.style.visibility = inView(target) ? "" : "hidden";
    const r = target.getBoundingClientRect();
    Object.assign(el.style, { left: `${r.left - 4}px`, top: `${r.top - 4}px`, width: `${r.width + 8}px`, height: `${r.height + 8}px` });
    frame = win.requestAnimationFrame(place);
  };
  const timer = win.setTimeout(() => stop(), RING_MS);
  const stop = () => {
    win.cancelAnimationFrame(frame);
    win.clearTimeout(timer);
    doc.removeEventListener("pointerdown", stop, true);
    el.remove();
  };
  doc.addEventListener("pointerdown", stop, true);
  place();
  return stop;
}

/**
 * Wait for the target to be drawn, scroll it into view if it is not, then ring it. One that is never
 * laid out (the phone layout draws another page) gets no ring. Returns a cancel, which also takes a
 * ring already drawn away.
 */
export function ringWhenFound(doc: Document, role: string, name: string | RegExp, skip?: Element | null): () => void {
  const win = doc.defaultView!;
  const until = Date.now() + WAIT_MS;
  let off: (() => void) | null = null;
  let timer = 0;
  const look = () => {
    const el = findByRole(doc, role, name, skip);
    if (el) {
      el.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      off = ring(el);
    }
    else if (Date.now() < until) timer = win.setTimeout(look, 100);
  };
  // Not at once: the router draws the new page after the popstate that asked for it, and the old one may hold a match.
  timer = win.setTimeout(look, 100);
  return () => {
    win.clearTimeout(timer);
    off?.();
  };
}
