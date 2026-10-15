import type { Change, Problem } from "../../templates/draft/types";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import { capKey, KEYS } from "./keys";
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

/** `budget.work_item_usd` as the draft holds it: the model's own value, not a derived one. */
export function outerValue(ctx: Ctx): number | null {
  const m = ctx.draft.view!.result.model["policy.yaml"] as { budget?: { work_item_usd?: number | null } } | undefined;
  return m?.budget?.work_item_usd ?? null;
}

/** A Limits scope that sets nothing: no default, maximum or lower cap, no draft change, no problem. Both screens fold it into one line. */
export function scopeUnset(ctx: Ctx, level: string): boolean {
  const caps = ctx.p.limits.caps[level];
  const keys = Object.keys(caps).flatMap((cap) => [capKey("default", level, cap).key, capKey("maximum", level, cap).key]);
  const outer = level === "work_item";
  return !ctx.problems.some((x) => x.level === level)
    && keys.every((k) => !ctx.changes.has(k) && !problemAt(ctx, k))
    && Object.values(caps).every((c) => c.default.value == null && c.maximum.value == null && !c.below?.length)
    && (!outer || (outerValue(ctx) == null && !ctx.changes.has(KEYS.outer.key) && !problemAt(ctx, KEYS.outer.key)));
}

