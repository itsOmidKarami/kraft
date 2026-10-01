import type { Problem } from "./draft/types";

/** The one word a problem item shows under its glyph (Decisions §9 Problems
 *  while editing). The server sends a sentence; this picks its gist.
 *  ponytail: keyword match on pydantic's and config_check's wording; a new
 *  message shape falls back to "invalid". */
export function problemWord(p: Problem): string {
  const m = p.message.toLowerCase();
  if (m.includes("must not be empty") || m.includes("no step") || m.includes("no task")) return "empty";
  if (m.includes("field required") || m.includes("missing")) return "missing";
  if (m.includes("unknown") || m.includes("not found") || m.includes("no node") || m.includes("does not name")) return "broken";
  if (m.includes("taken") || m.includes("duplicate")) return "duplicate";
  return "invalid";
}

/** A problem's sentence without pydantic's "Value error, " prefix. */
export const problemText = (p: Problem) => p.message.replace(/^Value error, /, "");
