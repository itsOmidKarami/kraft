import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { backLabel, parentOf, screenKey } from "./route";

/** What each history entry showed, by its index (react-router keeps `idx` in
 *  history.state). Memory only: after a reload the trail is empty and Back
 *  falls back to the parent, which is the right answer for a deep link. */
const trail = new Map<number, string>();
const idxNow = () => (window.history.state as { idx?: number } | null)?.idx ?? 0;

/** Records the screen shown at the current history index. */
export function useTrail() {
  const { pathname, search } = useLocation();
  useEffect(() => void trail.set(idxNow(), pathname + search), [pathname, search]);
}

/** Tests only. */
export const resetTrail = () => trail.clear();

/** Back for the current screen: one step back when the entry before it is the
 *  parent, otherwise a replace to the parent, so Back neither leaves the app
 *  nor loops (A.3). */
export function useBack() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const href = pathname + search;
  const parent = parentOf(href);
  return {
    label: backLabel(href),
    parent,
    go: () => {
      if (!parent) return;
      const prev = trail.get(idxNow() - 1);
      if (prev !== undefined && screenKey(prev) === screenKey(parent)) navigate(-1);
      else navigate(parent, { replace: true });
    },
  };
}
