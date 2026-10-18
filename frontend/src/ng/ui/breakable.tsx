import { Fragment, type ReactNode } from "react";

/** An id that wraps after its separators, never in the middle of a word. A dotted id breaks only at its dots, so a
 *  snake-case word stays whole: `verification.fix_loop` breaks after `verification.`. An id with no dot breaks after
 *  its underscores and dashes. The text is unchanged for a screen reader and for copy. */
export function breakable(text: string): ReactNode {
  const parts = text.split(/[./]/.test(text) ? /(?<=[./])/ : /(?<=[_-])/);
  if (parts.length < 2) return text;
  return parts.map((p, i) => <Fragment key={i}>{i > 0 && <wbr />}{p}</Fragment>);
}

/** Prose that may hold a path, such as a stop's reason: a break after each slash and nowhere else, so `$5.00`,
 *  `local.cfg` and `code_review` stay whole. Not after a path's leading slash, which would end a line on a lone
 *  "/": the space before it is the break. The text is unchanged for a screen reader and for copy. */
export function breakPaths(text: string): ReactNode {
  const parts = text.split(/(?<=\S\/)/);
  if (parts.length < 2) return text;
  return parts.map((p, i) => <Fragment key={i}>{i > 0 && <wbr />}{p}</Fragment>);
}
