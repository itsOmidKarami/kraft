import type { Change, Problem } from "../../templates/draft/types";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import type { PolicyResolved } from "./types";

/** What each section reads: the draft, the server's resolve of it, the changes by key and the problems. */
export interface Ctx {
  draft: ConfigDraft;
  p: PolicyResolved;
  changes: Map<string, Change>;
  problems: Problem[];
  /** Items running now (`GET /policy`'s `active_count`), for the slot line. */
  active: number | null;
}

/** The first problem about a key (`field` or `path` is it), for a cell to wear. */
export const problemAt = (ctx: Ctx, key: string) => ctx.problems.find((x) => x.field === key || x.path === key);
