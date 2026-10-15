import type { Analytics } from "../../types";

/** What the API takes for `range`, less `all`, which the weekly chart cannot show. Both layouts offer these. */
export const RANGES = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "8w", label: "8 weeks" },
  { value: "90d", label: "90 days" },
];
export const DEFAULT_RANGE = "8w";

/** The gates items waited at, each with the times that gate was rejected. */
export const gateRows = (r: Analytics) => {
  const rejected = new Map(r.rejected_gates_by_gate.map((g) => [g.gate, g.n]));
  return r.gate_waits.map((g) => ({ ...g, rejected: rejected.get(g.gate) ?? 0 }));
};

/** The "How this is counted" disclosure: one text for both layouts. */
export const HOW_COUNTED = [
  "Every figure covers items created in the range. A wait at a gate runs from the gate being requested until it is approved or rejected, kept apart from run time so a slow reviewer never reads as a slow agent.",
  "Bars count merges, not completions: a chain with no merge node completes without showing up.",
  "Minutes are the mean wall time of one worker session, over every item in range.",
  "Tokens are as recorded, never estimated. Dollars are what agents reported plus Kraft's estimate from its price table where they reported none, and a session it cannot price puts a \"+\" on the sum.",
];
