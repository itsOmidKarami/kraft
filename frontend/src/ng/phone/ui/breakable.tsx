import { Fragment, type ReactNode } from "react";

/** An id that wraps after its separators, never in the middle of a word. A dotted id breaks only at its dots, so a
 *  snake-case word stays whole: `verification.fix_loop` breaks after `verification.`. An id with no dot breaks after
 *  its underscores and dashes. The text is unchanged for a screen reader and for copy. */
export function breakable(text: string): ReactNode {
  const parts = text.split(/[./]/.test(text) ? /(?<=[./])/ : /(?<=[_-])/);
  if (parts.length < 2) return text;
  return parts.map((p, i) => <Fragment key={i}>{i > 0 && <wbr />}{p}</Fragment>);
}
