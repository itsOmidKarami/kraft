import { Fragment, type ReactNode } from "react";

/** A dotted or snake-case id that wraps after its separators, never in the middle of a word: `verification.fix_loop` breaks after the dot or the underscore. The text is unchanged for a screen reader and for copy. */
export function breakable(text: string): ReactNode {
  const parts = text.split(/(?<=[._/-])/);
  if (parts.length < 2) return text;
  return parts.map((p, i) => <Fragment key={i}>{i > 0 && <wbr />}{p}</Fragment>);
}
