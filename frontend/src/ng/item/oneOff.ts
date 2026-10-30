/** A one-off chain's custom content, as the editor and the Start review show it.
 *  Pure: the desktop review, the new-item page's create review and the phone sheet all read it. */
export interface CustomPart {
  /** Where in the authored chain (`add[0]`, `overrides.tests…`); empty for the whole chain. */
  path: string;
  /** One line saying what it is. */
  what: string;
  value: unknown;
}

export interface CustomRow {
  key: string;
  what: string;
  where: string;
  /** The part in full: a prompt or command as written, anything else as JSON. */
  text: string;
}

export const customText = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";

export const customRows = (custom: CustomPart[]): CustomRow[] =>
  custom.map((p, i) => ({ key: `${i}:${p.path}`, what: p.what, where: p.path || "the whole chain", text: customText(p.value) }));
