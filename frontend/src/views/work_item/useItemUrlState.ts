import { useLocation, useNavigate } from "react-router-dom";
import { type InspectorTab, type Selection } from "./selection";

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
 *  a single source of truth so a reload lands back exactly where it was. */
export function useItemUrlState(defaultNodeId: string | null) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));

  const write = (mutate: (p: URLSearchParams) => void, opts?: { replace?: boolean }) => {
    const next = new URLSearchParams(location.hash.replace(/^#/, ""));
    mutate(next);
    if (next.toString() === params.toString()) return; // no-op: nothing actually changed
    navigate({ hash: next.toString() }, opts);
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
    selectNode: (id: string) =>
      write((p) => {
        if (p.get("node") === id) return;
        p.set("node", id);
        for (const k of Object.values(SELECTION_KEY)) if (k) p.delete(k);
      }),
    setTab: (t: InspectorTab) => write((p) => p.set("tab", t)),
    // `replace: true` is for scroll-driven reselection (Diff's
    // IntersectionObserver): that fires on every file crossed, and pushing
    // for each would mean Back no longer leaves the page after ordinary
    // scrolling.
    select: (sel: Selection, opts?: { replace?: boolean }) =>
      write((p) => (sel.id && key ? p.set(key, sel.id) : key && p.delete(key)), opts),
    setMaximized: (on: boolean) => write((p) => (on ? p.set("max", "1") : p.delete("max"))),
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
    // `selectNode` in the same handler would stomp the tab change.
    goToNode: (t: InspectorTab, node: string) =>
      write((p) => {
        p.set("tab", t);
        if (p.get("node") !== node) {
          p.set("node", node);
          for (const k of Object.values(SELECTION_KEY)) if (k) p.delete(k);
        }
      }),
    reviewHref: (node: string, t: InspectorTab, id: string) => {
      const p = new URLSearchParams();
      p.set("node", node);
      p.set("tab", t);
      if (SELECTION_KEY[t] && id) p.set(SELECTION_KEY[t], id);
      return `#${p.toString()}`;
    },
  };
}
