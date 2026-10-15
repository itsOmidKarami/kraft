import { elapsed } from "../../format";
import type { Analytics } from "../../types";

/** What the API takes for `range`, less `all`, which the weekly chart cannot show. Both layouts offer these. */
export const RANGES = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "8w", label: "8 weeks" },
  { value: "90d", label: "90 days" },
];
export const DEFAULT_RANGE = "8w";

/** What both layouts say, in place of every figure, when the range holds no completed item and no node ran. */
export const EMPTY_RANGE = "Nothing has completed in this range yet.";

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

/** A node's mean wall time in whole minutes: "—" when it has no runs to average, "<1 min" under a minute, a true zero as 0. */
export const minutes = (ms: number, runs: number) => (runs === 0 ? "—" : ms > 0 && ms < 60_000 ? "<1 min" : String(Math.round(ms / 60_000)));

/** A span the report carries as 0 when nothing was measured (no completed item, no merge request): "—" then, not "0s". */
export const measured = (ms: number, has = ms > 0) => (has ? elapsed(ms) : "—");
