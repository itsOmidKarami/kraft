/** The id rule every chain component follows (`templates/environment.py`
 *  `_IDENTIFIER`, `models.py` `RESERVED_SEGMENTS`). The server checks again;
 *  this only lets the card say why before sending (Decisions §9 Invalid id). */
const ID = /^[a-z][a-z0-9_-]*$/;
export const RESERVED = ["main", "on_failure", "fix_loop", "judge", "escalation", "on_base_changed", "on_conflict"];

export function idError(id: string, taken: string[]): string | null {
  if (!id) return null;
  if (!ID.test(id)) return "Use lowercase letters, digits, _ and -, starting with a letter.";
  if (RESERVED.includes(id)) return `${id} is a reserved word.`;
  if (taken.includes(id)) return `${id} is taken.`;
  return null;
}
