import { useEffect, useState } from "react";
import { DRAFTS_CHANGED, listDrafts } from "../templates/draft/draftApi";
import type { DraftSummary } from "../templates/draft/types";

export type AreaCount = { draft: boolean; problems: number };

/** Which sidebar row each draft area belongs to (Decisions §10); W14 adds harnesses. */
const ROW_OF: Record<string, string> = {
  chains: "/templates/chains",
  library: "/templates/library",
  repos: "/settings/repos",
  policy: "/settings/policy",
  intake: "/settings/auto-intake",
  harnesses: "/settings/harnesses",
};

/** Per sidebar row path, whether its area has an open draft and how many
 *  problems its drafts have. Read on mount, on window focus and after any
 *  draft write (brief Decided 10). */
export function useDraftCounts(): Record<string, AreaCount> {
  const [list, setList] = useState<DraftSummary[]>([]);
  useEffect(() => {
    let live = true;
    const load = () => listDrafts().then((a) => { if (live && a.status === 200 && Array.isArray(a.body)) setList(a.body); }).catch(() => {});
    load();
    window.addEventListener("focus", load);
    window.addEventListener(DRAFTS_CHANGED, load);
    return () => {
      live = false;
      window.removeEventListener("focus", load);
      window.removeEventListener(DRAFTS_CHANGED, load);
    };
  }, []);
  const out: Record<string, AreaCount> = {};
  for (const d of list) {
    const row = ROW_OF[d.area];
    if (!row) continue;
    const c = (out[row] ??= { draft: false, problems: 0 });
    c.draft = true;
    c.problems += d.problems;
  }
  return out;
}
