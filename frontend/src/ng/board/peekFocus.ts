import { useEffect, type RefObject } from "react";

/** A board control the open peek covers, set `inert`. */
const MARK = "data-under-peek";
const CONTROLS = "button, a[href], input, select, textarea, [tabindex]";
const TABBABLE = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

const paneOf = (root: HTMLElement) => root.querySelector<HTMLElement>(":scope > aside.pane");
const tabbables = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>(TABBABLE)].filter((x) => !x.closest("[inert]") && x.getClientRects().length > 0);

/** The peek is a non-modal pane over the list's right edge (the WAI-ARIA
 *  complementary pattern, not a dialog): the list stays live for a pointer
 *  and nothing traps focus. What it covers is out of a pointer's reach, so it
 *  leaves the keyboard's too: a list control whose left edge sits under the
 *  peek is `inert` while the peek is open, and Tab never lands where nobody
 *  can see it. A control with any of its left edge in view stays, as a
 *  pointer still reaches it. In the tab order the peek follows the selected
 *  row, as if it were drawn under it. */
export function usePeekFocus(body: RefObject<HTMLElement | null>, sel: string, width: number) {
  useEffect(() => {
    const root = body.current;
    if (!sel || !root) return;
    const list = root.querySelector<HTMLElement>(".board-list");
    const rowOf = () => [...root.querySelectorAll<HTMLElement>("[data-row]")].find((r) => r.dataset.row === sel)?.querySelector<HTMLElement>(".board-row-main") ?? null;

    let frame = 0;
    const mark = () => {
      frame = 0;
      const pane = paneOf(root);
      const edge = pane ? pane.getBoundingClientRect().left : Infinity;
      const was = document.activeElement;
      for (const el of list?.querySelectorAll<HTMLElement>(CONTROLS) ?? []) {
        const under = el.getBoundingClientRect().left >= edge;
        if (under && !el.hasAttribute(MARK)) {
          el.setAttribute(MARK, "");
          el.setAttribute("inert", "");
        } else if (!under && el.hasAttribute(MARK)) {
          el.removeAttribute(MARK);
          el.removeAttribute("inert");
        }
      }
      // A control that had the focus and went under the peek (a row's Open, which opens it) hands it to the peek.
      if (pane && was instanceof HTMLElement && was.hasAttribute(MARK)) tabbables(pane)[0]?.focus();
    };
    const soon = () => { if (!frame) frame = requestAnimationFrame(mark); };
    mark();
    // Rows come and go, the peek loads and is dragged wider: mark again.
    const mo = new MutationObserver(soon);
    mo.observe(root, { childList: true, subtree: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(soon);
    ro?.observe(root);
    const pane = paneOf(root);
    if (pane) ro?.observe(pane);

    // The peek sits in the tab order right after the selected row: Tab from
    // the row goes in, Tab from its last control goes on to the next row.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || e.altKey || e.ctrlKey || e.metaKey) return;
      const pane = paneOf(root);
      const row = rowOf();
      const t = e.target;
      if (!pane || !row || !list || !(t instanceof HTMLElement)) return;
      const inPane = tabbables(pane);
      const inList = tabbables(list);
      const next = inList[inList.indexOf(row) + 1];
      const to = !e.shiftKey
        ? t === row ? inPane[0] : t === inPane.at(-1) ? next : undefined
        : t === inPane[0] ? row : t === next ? inPane.at(-1) : undefined;
      if (!to) return;
      e.preventDefault();
      to.focus();
    };
    root.addEventListener("keydown", onKey);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      mo.disconnect();
      ro?.disconnect();
      root.removeEventListener("keydown", onKey);
      for (const el of root.querySelectorAll<HTMLElement>(`[${MARK}]`)) {
        el.removeAttribute(MARK);
        el.removeAttribute("inert");
      }
    };
  }, [body, sel, width]);
}
