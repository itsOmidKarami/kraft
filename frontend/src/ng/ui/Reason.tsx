import { useId, type ReactNode } from "react";
import "./ui.css";

/** Why a control is disabled, as text beside it: `attrs` goes on the control (aria-describedby), `note`
 *  next to it (a tree with its own element takes `id` and `text`). Null while the control works. A `title` on top is only ever a hint, never the carrier. A resting state
 *  (nothing started that the control blocks) stays quiet; the reason shows once something is. */
export function useReason(reason: string | null | undefined, how: { below?: boolean; quiet?: boolean } = {}): { attrs: { "aria-describedby"?: string }; note: ReactNode; id: string; text: string | null } {
  const id = useId();
  const text = reason ?? null;
  // `below` puts it on a line of its own under a footer's buttons; `quiet` keeps it for screen readers until it matters.
  const cls = `reason${how.below ? " is-below" : ""}${how.quiet ? " is-quiet" : ""}`;
  return text
    ? { attrs: { "aria-describedby": id }, note: <span className={cls} id={id}>{text}</span>, id, text }
    : { attrs: {}, note: null, id, text };
}
