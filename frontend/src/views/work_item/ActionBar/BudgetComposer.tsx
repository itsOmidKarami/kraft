import { useState } from "react";
import * as api from "../../../api";

/** Screen 23's budget composer: pills `+$5`/`+$10`/`no cap` (showing the
 *  resulting cap), submit "Raise to $N and resume" (m07 copy). */
export function BudgetComposer({
  itemId,
  capUsd,
  spentUsd,
  busy,
  err,
  run,
  onCancel,
}: {
  itemId: string;
  capUsd: number;
  /** `item.budget.spent_usd` — "spent so far $10.38 of $15" (spec §3). */
  spentUsd?: number;
  busy: boolean;
  err: string | null;
  run: (fn: () => Promise<unknown>, toast?: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [next, setNext] = useState<number | null>(capUsd + 5);
  const pills: { label: string; value: number | null }[] = [
    { label: "+$5", value: capUsd + 5 },
    { label: "+$10", value: capUsd + 10 },
    { label: "no cap", value: null },
  ];
  return (
    <div className="composer">
      <p className="composer-head">
        Raise the budget for this item
        {spentUsd != null && (
          <span className="composer-explain">
            {" "}
            · spent so far ${spentUsd.toFixed(2)} of ${capUsd}
          </span>
        )}
      </p>
      <div className="gate-actions">
        {pills.map((p) => (
          <button
            key={p.label}
            type="button"
            className="chip"
            aria-pressed={next === p.value}
            onClick={() => setNext(p.value)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="gate-actions">
        <button
          className="btn btn-primary"
          disabled={busy}
          onClick={() =>
            run(
              () => api.raiseBudget(itemId, next),
              next == null ? "Budget cap removed" : `Budget raised to $${next}`,
            )
          }
        >
          {next == null
            ? "Remove cap and resume"
            : `Raise to $${next} and resume`}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
