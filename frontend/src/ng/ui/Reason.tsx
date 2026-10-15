import { useId, type ReactNode } from "react";
import "./ui.css";

/** Why a control is disabled, as text beside it: `attrs` goes on the control (aria-describedby), `note`
 *  next to it (a tree with its own element takes `id` and `text`). Null while the control works. A `title` on top is only ever a hint, never the carrier. */
export function useReason(reason: string | null | undefined): { attrs: { "aria-describedby"?: string }; note: ReactNode; id: string; text: string | null } {
  const id = useId();
  const text = reason ?? null;
  return text
    ? { attrs: { "aria-describedby": id }, note: <span className="reason" id={id}>{text}</span>, id, text }
    : { attrs: {}, note: null, id, text };
}
