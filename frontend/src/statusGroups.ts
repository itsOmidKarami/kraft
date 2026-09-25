import type { DerivedState } from "./deriveState";

export const STATUS_GROUPS: { id: string; label: string; test: (d: DerivedState) => boolean }[] = [
  { id: "needs", label: "Needs you", test: (d) => d.needsYou },
  {
    id: "running",
    label: "Running",
    // A rate-limited or waiting item is not mid-agent-call, but it is not
    // waiting on a person either -- the poller drives it forward on its own,
    // the same story "Running" already tells for an active item. Leaving
    // either out would drop it off the main view, which is "a slow run
    // looks like a hung one" from the other direction. An escalating item is
    // an agent at work too, until it posts its message (W11 · J.1).
    test: (d) => ["running", "rate_limited", "waiting", "escalating"].includes(d.state),
  },
  { id: "not_started", label: "Not started", test: (d) => d.state === "not_started" },
  {
    id: "done",
    label: "Done",
    // Design 06: "Done N · completed and abandoned items" -- an abandoned
    // item is Done for the board's purposes even though `deriveState` keeps
    // its own distinct `abandoned` display state (its glyph/tone differ).
    test: (d) => ["done", "abandoned"].includes(d.state),
  },
];
