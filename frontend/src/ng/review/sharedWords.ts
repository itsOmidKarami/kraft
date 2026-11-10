import type { ThreadLabel } from "../../types";

/** Wording that the desktop and phone trees both show: one source, imported by each. */
export const LINES_COMMENTED_ON = "Lines commented on";
export const ON_THE_WHOLE_CHANGE = "On the whole change";

/** A thread's label as a count line says it: lowercase, hyphenated. Not `LABELS` (the composer's "Must fix") nor `TAG`
 *  (the tag's "MUST FIX"), both in `review/Thread.tsx`: those are other places' words on purpose. */
export const LABEL_WORD: Record<ThreadLabel, string> = { must_fix: "must-fix", question: "question", nit: "nit" };
