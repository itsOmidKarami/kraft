const SR_ONLY = /sr-only|visually-hidden/;

/** The text a button shows: not what an aria-hidden or screen-reader-only part holds. */
const visibleText = (n: Node): string => {
  if (n instanceof Text) return n.data;
  if (!(n instanceof HTMLElement) || n.getAttribute("aria-hidden") === "true" || SR_ONLY.test(n.className.toString())) return "";
  return [...n.childNodes].map(visibleText).join("");
};

/** What is wrong with an icon-only button (one whose visible text has no letter or digit: an icon, or a glyph
 *  like × or ↑), or null: it needs an accessible name, and the tooltip `ui/Tooltip` shows (`data-tip`, which
 *  `tip()` and IconButton set). A button with text, or one hidden from assistive technology, is not asked. */
export function iconOnlyProblem(b: HTMLElement): string | null {
  const role = b.getAttribute("role");
  if (role && role !== "button") return null;
  if (b.closest('[aria-hidden="true"]') || /[\p{L}\p{N}]/u.test(visibleText(b))) return null;
  if (!(b.hasAttribute("aria-label") || b.hasAttribute("aria-labelledby") || /[\p{L}\p{N}]/u.test(b.textContent ?? ""))) return "has no accessible name";
  return b.hasAttribute("data-tip") ? null : "has no tooltip";
}
