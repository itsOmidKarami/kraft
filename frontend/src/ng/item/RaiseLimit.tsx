import { useState } from "react";
import type { StopLimit } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { act } from "./actions";

const WHAT: Record<StopLimit["key"], { label: (path: string) => string; unit: string }> = {
  max_attempts: { label: (p) => `Fix attempts on ${p}`, unit: "attempts" },
  timeout_minutes: { label: (p) => `Fix-loop time on ${p}`, unit: "minutes" },
  time_cap_minutes: { label: () => "Running-time cap", unit: "minutes" },
  total_time_cap_minutes: { label: () => "Total-time cap", unit: "minutes" },
};

/** The item-policy PATCH that sets `limit` to `n`: item-wide, or on the fix loop's node. */
export const raiseBody = (limit: StopLimit, n: number) => ({ policy: limit.path ? { paths: { [limit.path]: { [limit.key]: n } } } : { [limit.key]: n } });

/** A cap stop's one limit, raised: PATCH the item's policy, then retry the node. */
export function RaiseLimit({ itemId, limit, onClose, onDone }: { itemId: string; limit: StopLimit; onClose: () => void; onDone: () => void }) {
  const { label, unit } = WHAT[limit.key];
  const [text, setText] = useState(String(limit.value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const n = Number(text);
  const valid = Number.isInteger(n) && n > limit.value;
  const save = async () => {
    setBusy(true);
    setError(null);
    const set = await act.patch(itemId, raiseBody(limit, n));
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
          hint={`Now ${limit.value}. ${limit.maximum == null ? "No maximum." : `Maximum ${limit.maximum}.`}`}
          error={error}
        >
          <input data-autofocus className="item-input" type="number" inputMode="numeric" min={limit.value + 1} max={limit.maximum ?? undefined} step={1} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
      </form>
    </Dialog>
  );
}
