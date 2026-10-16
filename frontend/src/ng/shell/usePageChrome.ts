import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useStore } from "../../store";
import { useGroupCount } from "../board/counts";
import { usePageItem } from "./pageItem";
import { pageTitle } from "./pageName";

/** Sets the tab title from the route, desktop and phone alike. */
export function useDocumentTitle() {
  const { pathname, search } = useLocation();
  const items = useStore((s) => s.workItems);
  const page = usePageItem((s) => s.item);
  const needs = useGroupCount("needs");
  const id = /^\/work-items\/([^/]+)/.exec(pathname)?.[1];
  const known = id !== undefined && page?.id === decodeURIComponent(id) ? page : undefined;
  const title = pageTitle(pathname + search, (i) => known ?? items[i], needs);
  useEffect(() => void (document.title = title), [title]);
}

/** Where a page change may move focus from: nothing yet (the link that was
 *  pressed went away with its page), the page itself, or the navigation. An
 *  open dialog, a field or the composer holds focus on purpose. */
const mayTake = (at: Element | null, main: HTMLElement) =>
  !at || at === document.body || at === main || !at.closest('[role="dialog"]') && !!at.closest(".ng-side, .ng-header, .ng-skip, .ph-tabs, .ph-header");

/** On a route change (not a tab or a query change within a page), moves focus
 *  to the page's heading so a keyboard or screen-reader user lands on the new
 *  page, not on the link they pressed. The heading may arrive after the page's
 *  data, so it is looked for briefly. `key` is what makes a new page. */
export function useFocusHeading(mainId: string, key: string) {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) return void (first.current = false);
    const main = document.getElementById(mainId);
    if (!main) return;
    let done = false;
    const take = () => {
      if (done) return true;
      if (!mayTake(document.activeElement, main) && document.activeElement !== main) return (done = true);
      const h = main.querySelector<HTMLElement>("h1");
      if (!h) return false;
      h.tabIndex = -1;
      h.focus({ preventScroll: true });
      return (done = true);
    };
    const stop = () => { done = true; obs.disconnect(); clearTimeout(timer); };
    const obs = new MutationObserver(() => take() && stop());
    const timer = setTimeout(() => { if (!take()) main.focus({ preventScroll: true }); stop(); }, 1000);
    // Search and the sidebar's links run on the click that changed the route, so wait for the page to render.
    requestAnimationFrame(() => (take() ? stop() : obs.observe(main, { childList: true, subtree: true })));
    return stop;
  }, [mainId, key]);
}
