import { useEffect, useState } from "react";
import { create } from "zustand";
import { dollars, dollarsText, DOLLARS_HINT } from "../../format";
import type { StopLimit, WorkItem } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { act } from "./actions";
import { limitPolicy } from "./limitPolicy";

const WHAT: Record<StopLimit["key"], { label: (path: string) => string; unit: string; money?: true }> = {
  budget_usd: { label: () => "Budget cap", unit: "dollars", money: true },
  max_attempts: { label: (p) => `Fix attempts on ${p}`, unit: "attempts" },
  timeout_minutes: { label: (p) => `Fix-loop time on ${p}`, unit: "minutes" },
  time_cap_minutes: { label: () => "Running-time cap", unit: "minutes" },
  total_time_cap_minutes: { label: () => "Total-time cap", unit: "minutes" },
};

/** A request to open a cap stop's RaiseLimit, which the Banner owns: the
 *  header's, the peek's and the review page's Raise cap ask, as the banner's
 *  own button does (R12b-06; a time cap's led to Config, which has no row for
 *  it). As `openBudgetEditor`: for one item, taken once, within a few seconds. */
const useLimitAsk = create<{ id: string | null; at: number }>(() => ({ id: null, at: 0 }));
const LIMIT_ASK_MS = 5_000;
export const openLimitEditor = (id: string, at = Date.now()) => useLimitAsk.setState({ id, at });
export function useLimitAsked(id: string, take: () => void) {
  const asked = useLimitAsk((s) => s.id === id && Date.now() - s.at < LIMIT_ASK_MS);
  useEffect(() => {
    if (!asked) return;
    useLimitAsk.setState({ id: null, at: 0 });
    take();
    // Once per ask: `take` is a fresh closure each render.
  }, [asked]);
}

/** A cap stop's one limit, raised: PATCH the item's policy, keeping the rest
 *  of its override (`override`), then retry the node. */
export function RaiseLimit({ itemId, limit, override, onClose, onDone }: { itemId: string; limit: StopLimit; override: WorkItem["policy_override"]; onClose: () => void; onDone: () => void }) {
  const { label, unit, money } = WHAT[limit.key];
  const show = (v: number) => (money ? `$${v}` : String(v));
  const [text, setText] = useState(money ? dollarsText(limit.value) : String(limit.value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Dollars are read one way everywhere (`dollars`): a number field turned "0,5" into 05 (R13b-01).
  const n = money ? dollars(text) : Number(text);
  const valid = (money ? Number.isFinite(n) : Number.isInteger(n)) && n > limit.value && (!money || limit.maximum == null || n <= limit.maximum);
  const save = async () => {
    setBusy(true);
    setError(null);
    const set = await act.patch(itemId, limitPolicy(override, limit, n));
    if (!set.ok) { setBusy(false); return setError(set.error); }
    const retried = await act.retry(itemId);
    setBusy(false);
    if (!retried.ok) return setError(`Raised to ${n}, but the retry failed: ${retried.error}`);
    onDone();
  };
  return (
    <Dialog
      title={`Raise ${label(limit.path).toLowerCase()}`}
      onClose={onClose}
      dirty={busy}
      className="rl-dialog"
      footer={<>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" disabled={!valid || busy} onClick={save}>Save &amp; retry</Button>
      </>}
    >
      <form className="rl-form" onSubmit={(e) => { e.preventDefault(); if (valid && !busy) void save(); }}>
        <Field
          label={`${label(limit.path)} (${unit})`}
          hint={`Now ${show(limit.value)}. ${limit.maximum == null ? "No maximum." : `Maximum ${show(limit.maximum)}.`}`}
          error={error ?? (money && text.trim() && Number.isNaN(n) ? DOLLARS_HINT : null)}
        >
          <input data-autofocus className="item-input" type={money ? "text" : "number"} inputMode={money ? "decimal" : "numeric"} {...(!money && { min: limit.value + 1, max: limit.maximum ?? undefined, step: 1 })} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
      </form>
    </Dialog>
  );
}
