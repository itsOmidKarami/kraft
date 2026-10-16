import type { HTMLAttributes } from "react";
import type { Tone } from "./tone";
import "./ui.css";

export type { Tone };

/** A read-only label: a status, kind, count or name. Without a tone it is outlined; `warn`
 *  still means only "needs a human", and `change` marks a draft or changed value (never amber).
 *  `mono` is for ids and caps status words. A control you press is a Chip. */
export function Badge({ tone, mono, className, ...rest }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone; mono?: boolean }) {
  const cls = ["badge", tone && `is-${tone}`, mono && "is-mono", className].filter(Boolean).join(" ");
  return <span className={cls} {...rest} />;
}
