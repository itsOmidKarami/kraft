import { detailOf } from "../../http";
import type { Change, Problem } from "../../templates/draft/types";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import { ValueCell } from "../../templates/draft/ValueCell";
import type { KeySpec } from "./keys";
import { parse, parseAge, parseSize, raw, show } from "./units";

/** `set_value` for one key; null on success, the server's refusal's message otherwise.
 *  A problem the value creates is not a refusal: the draft holds it, the page shows it
 *  on its cell and Publish waits (Decisions §12). */
export async function setValue(draft: ConfigDraft, k: Pick<KeySpec, "scope" | "key">, value: unknown): Promise<string | null> {
  const a = await draft.ops([{ op: "set_value", scope: k.scope, key: k.key, value }], { quiet: true });
  return a.status === 200 ? null : detailOf(a.body);
}

/** The "was" half of a change row's "was → now" summary. */
export const was = (c: Change | undefined) => (c ? c.summary.split(" → ")[0] : undefined);

/** One editable policy value: its number as the page writes it, the published value
 *  struck through beside a changed one, a problem on it in red. `inherited` names the
 *  level a maximum comes from when this level sets none. */
export function PolicyCell({ draft, k, label, value, bound, placeholder, change, problem, inherited }: {
  draft: ConfigDraft;
  k: KeySpec;
  label: string;
  /** A size (`10G`) or an age (`24h`) is text; every other unit is a number. */
  value: number | string | null;
  /** An unset value reads "no bound", not "not set". */
  bound?: boolean;
  /** What an unset size reads as. */
  placeholder?: string;
  change?: Change;
  problem?: Problem;
  inherited?: string | null;
}) {
  const size = k.unit === "size" || k.unit === "age";
  const commit = (text: string) => {
    const p = k.unit === "size" ? parseSize(text) : k.unit === "age" ? parseAge(text) : parse(k.unit, text, k.zero);
    return "error" in p ? p.error : setValue(draft, k, p.value);
  };
  const num = typeof value === "number" ? value : null;
  const text = size ? (value ?? placeholder ?? "not set") : show(k.unit, num, bound);
  const start = size ? String(value ?? "") : raw(k.unit, num);
  return (
    <span className="pol-cell">
      <ValueCell label={label} value={start} display={String(text)} muted={value == null} changed={!!change} bad={!!problem} onCommit={commit} />
      {change && <s className="pol-was" aria-label={`was ${was(change)}`}>{was(change)}</s>}
      {inherited && value != null && <span className="pol-from">from {inherited.replace("_", " ")}</span>}
    </span>
  );
}
