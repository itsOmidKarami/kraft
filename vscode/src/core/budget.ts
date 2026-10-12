/** Raise Budget's input, read as `kraft item raise-budget --usd` reads it
 *  (`_budget` in `src/kraft/cli/item.py`): dollars, or `none` for no cap. A
 *  cap of 0 or less is refused here: with spend the server never saw it would
 *  store it and the item would stop again at once. A positive number the server
 *  refuses (not above what was spent) is its 422 to say. */
export function parseUsd(raw: string): { usd: number | null } | { error: string } {
  const text = raw.trim();
  if (text.toLowerCase() === "none") return { usd: null };
  const usd = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text) ? Number(text) : NaN;
  if (!Number.isFinite(usd)) return { error: `dollars or \`none\`, not '${raw}'` };
  return usd > 0 ? { usd } : { error: `a positive number of dollars, or \`none\`, not '${raw}'` };
}
