import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import type { BoardGroupBy } from "../../types";
import type { GroupBy, SortBy } from "./model";

/** The board's state in its URL query (W6 brief A.5), so a crumb, a reload
 *  and the draft page's way back land on the same view. Written with
 *  `replace`: a filter is not a page to go Back to. */
export interface BoardQuery {
  repo: string;
  chain: string;
  q: string;
  group: GroupBy;
  sort: SortBy;
  /** The row open in the peek. */
  sel: string;
  /** The composer is open. */
  new: boolean;
}

const GROUPS: GroupBy[] = ["status", "repo", "chain"];
const SORTS: SortBy[] = ["attention", "updated", "created", "title"];

/** `theme.board.group_by` names the chain grouping `template`. */
export const groupFromTheme = (g: BoardGroupBy | undefined): GroupBy => (g === "template" ? "chain" : g === "repo" ? "repo" : "status");

export function readBoardQuery(p: URLSearchParams, themeGroup?: BoardGroupBy): BoardQuery {
  const group = p.get("group") as GroupBy;
  const sort = p.get("sort") as SortBy;
  return {
    repo: p.get("repo") ?? "",
    chain: p.get("chain") ?? "",
    q: p.get("q") ?? "",
    group: GROUPS.includes(group) ? group : groupFromTheme(themeGroup),
    sort: SORTS.includes(sort) ? sort : "attention",
    sel: p.get("sel") ?? "",
    new: p.get("new") === "1",
  };
}

/** The query with `patch` applied; empty values and defaults are left out. */
export function writeBoardQuery(p: URLSearchParams, patch: Partial<BoardQuery>): URLSearchParams {
  const next = new URLSearchParams(p);
  for (const [k, v] of Object.entries(patch)) {
    const s = v === true ? "1" : v === false ? "" : String(v ?? "");
    if (!s || (k === "sort" && s === "attention")) next.delete(k);
    else next.set(k, s);
  }
  return next;
}

export function useBoardQuery(themeGroup?: BoardGroupBy): [BoardQuery, (patch: Partial<BoardQuery>) => void] {
  const [params, setParams] = useSearchParams();
  const set = useCallback((patch: Partial<BoardQuery>) => setParams((p) => writeBoardQuery(p, patch), { replace: true }), [setParams]);
  return [readBoardQuery(params, themeGroup), set];
}
