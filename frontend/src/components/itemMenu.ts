import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { showToast } from "./Toast";
import type { OverflowItem } from "./ui";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The one item menu (W0.9): the app header's `…` on an item page and the
 *  action bar's `…` hold the same four entries on every state — Archive (or
 *  Restore once archived), Open worktree, Copy id, Copy link. */
export function itemMenuItems(item: WorkItem): OverflowItem[] {
  const refresh = () => useStore.getState().hydrateItem(item.id).catch(() => {});
  const act = (fn: () => Promise<unknown>, done: string) => () => {
    fn().then(
      () => {
        refresh();
        showToast(done);
      },
      (e) => showToast(message(e)),
    );
  };
  const copy = (text: string, what: string) => () => {
    const write = navigator.clipboard?.writeText(text) ?? Promise.reject(new Error("clipboard unavailable"));
    write.then(
      () => showToast(`Copied ${what}`),
      () => showToast(`Could not copy ${what}`),
    );
  };
  return [
    item.archived_at
      ? { label: "Restore", onSelect: act(() => api.restoreWorkItem(item.id), "Restored") }
      : { label: "Archive", onSelect: act(() => api.archiveWorkItem(item.id), "Archived") },
    { label: "Open worktree", onSelect: act(() => api.openWorktree(item.id), "Opened the worktree") },
    { label: "Copy id", onSelect: copy(item.id, "id") },
    { label: "Copy link", onSelect: copy(`${window.location.origin}/work-items/${item.id}`, "link") },
  ];
}
