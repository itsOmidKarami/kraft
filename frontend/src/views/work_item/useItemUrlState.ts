import { useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { type InspectorTab, type Selection } from "./selection";
import { usePhone } from "./usePhone";

/** Which hash key holds each tab's selected row. They coexist, so switching
 *  tabs restores what that tab had — and a reload does too, which the old
 *  `selectionByTab` React state could not. */
export const SELECTION_KEY: Record<InspectorTab, string> = {
  tasks: "session", changes: "file", documents: "doc", timeline: "tnode", config: "",
};

const KIND: Record<InspectorTab, Selection["kind"]> = {
  tasks: "session", changes: "file", documents: "document",
  timeline: "timeline-node", config: "session",
};

const TABS: InspectorTab[] = ["tasks", "changes", "documents", "timeline", "config"];

/** The item page's whole selection state, backed by one hash:
 *  `#node=<id>&tab=<tab>&<selection key>=<id>&max=1`. Replaces
 *  `useNodeSelection`, `useMaximized` and the `selectionByTab` React state —
 *  a single source of truth so a reload lands back exactly where it was.
 *
 *  UI v3 spec 2026-09-12 §2: on desktop the item page is one history level —
 *  every write below **replaces** by default, so one Back press leaves the
 *  page. `opts?.push` is the escape hatch, used in exactly two places:
 *  `setMaximized(true)` (entering fullscreen pushes, so Back leaves
 *  fullscreen rather than the page — §2.1b), and — phone only —
 *  `selectNode`/`goToNode` (opening the full-screen node page, m05, is a
 *  real page transition there, not a same-page selection — §2.1a, gated on
 *  `usePhone()` since it's the same function serving the desktop tap too).
 *  §1 deleted the one caller that used to request a push through `select`
 *  itself (`Diff`'s scroll-driven reselection), so `select` no longer takes
 *  opts at all — every other write already wants the (now-default) replace.
 */
export function useItemUrlState(defaultNodeId: string | null) {
  const location = useLocation();
  const navigate = useNavigate();
  const phone = usePhone();
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));

  // Whether *this* mount pushed the maximize entry currently open — so
  // leaving fullscreen knows to consume it with `navigate(-1)` rather than
  // write a same-page replace on top of it. A deep link or reload straight
  // onto `#…&max=1` starts this `false`: there is no entry to return to, so
  // leaving writes a replace instead (spec §2.1b).
  const pushedMaxRef = useRef(false);

  const write = (mutate: (p: URLSearchParams) => void, opts?: { push?: boolean }) => {
    const next = new URLSearchParams(location.hash.replace(/^#/, ""));
    mutate(next);
    if (next.toString() === params.toString()) return; // no-op: nothing actually changed
    navigate({ hash: next.toString() }, opts?.push ? undefined : { replace: true });
  };

  const fromHash = params.get("node");
  const rawTab = params.get("tab");
  const tab = (TABS.includes(rawTab as InspectorTab) ? rawTab : "tasks") as InspectorTab;
  const key = SELECTION_KEY[tab];

  return {
    nodeId: fromHash ?? defaultNodeId,
    nodeExplicit: fromHash != null,
    tab,
    selection: { kind: KIND[tab], id: key ? params.get(key) : null } as Selection,
    maximized: params.get("max") === "1",
    // Changing node clears every tab's selection, the way `selectNode` +
    // `setSelectionByTab(EMPTY)` used to: a `session=` or `tnode=` left over
    // from the previous node points the right pane at that node's log while
    // the list beside it (filtered by the new node) highlights nothing.
    // Phone only: opening a node is the m05 page transition, so it pushes;
    // desktop replaces, like every other selection (spec §2.1a).
    selectNode: (id: string) =>
      write((p) => {
        if (p.get("node") === id) return;
        p.set("node", id);
        for (const k of Object.values(SELECTION_KEY)) if (k) p.delete(k);
      }, phone ? { push: true } : undefined),
    setTab: (t: InspectorTab) => write((p) => p.set("tab", t)),
    select: (sel: Selection) =>
      write((p) => (sel.id && key ? p.set(key, sel.id) : key && p.delete(key))),
    // Entering pushes (so Back leaves fullscreen, not the page); leaving
    // consumes that same entry with `navigate(-1)` if this mount is the one
    // that pushed it, or replaces if there was nothing to return to (spec
    // §2.1b). The rule lives here, not at each of index.tsx's three exit
    // points (Escape, Restore, the pane's own toggle) — they all just call
    // `setMaximized(false)`.
    setMaximized: (on: boolean) => {
      if (on) {
        pushedMaxRef.current = true;
        write((p) => p.set("max", "1"), { push: true });
        return;
      }
      if (pushedMaxRef.current) {
        pushedMaxRef.current = false;
        navigate(-1);
        return;
      }
      write((p) => p.delete("max"));
    },
    // Switching tab and setting/clearing that tab's own selection is one
    // history entry, not two: `setTab` then `select` in the same handler
    // would each read the pre-navigation hash, so the second call would
    // stomp the first's tab change with a stale one.
    goTo: (t: InspectorTab, id?: string | null) =>
      write((p) => {
        p.set("tab", t);
        const k = SELECTION_KEY[t];
        if (!k) return;
        if (id) p.set(k, id);
        else p.delete(k);
      }),
    // Same one-write hazard as `goTo`, but for opening a phone node page on a
    // given tab (`node` param, not a tab's own selection key) — `setTab` then
    // `selectNode` in the same handler would stomp the tab change. Same
    // phone/page-transition exception as `selectNode` above — this is only
    // ever called on phone (index.tsx's `goToChanges`/`goToConfig`).
    goToNode: (t: InspectorTab, node: string) =>
      write((p) => {
        p.set("tab", t);
        if (p.get("node") !== node) {
          p.set("node", node);
          for (const k of Object.values(SELECTION_KEY)) if (k) p.delete(k);
        }
      }, phone ? { push: true } : undefined),
    reviewHref: (node: string, t: InspectorTab, id: string) => {
      const p = new URLSearchParams();
      p.set("node", node);
      p.set("tab", t);
      if (SELECTION_KEY[t] && id) p.set(SELECTION_KEY[t], id);
      return `#${p.toString()}`;
    },
  };
}
