import { useEffect, useState } from "react";
import { detailOf, request } from "../http";

/** `GET /editors`: the editors whose executable this machine has, whether the
 *  system opener is there, and the default chosen in Settings › Appearance. */
export type Editors = { available: string[]; system: boolean; default: string | null };

/** The `editor` that asks `POST …/open` for the system's opener by name. `null`
 *  asks for the default (theme.yaml `editor`, else KRAFT_EDITOR, else the
 *  system's), so "System default" sent as null launched KRAFT_EDITOR while the
 *  note said "Opened in System default" (#502 review). */
export const SYSTEM_EDITOR = "system";

const NAMES: Record<string, string> = { code: "VS Code", cursor: "Cursor", zed: "Zed", obsidian: "Obsidian", [SYSTEM_EDITOR]: "System default" };
/** An editor's name; null (no editor chosen) and `system` are the system's default app. */
export const editorName = (id: string | null) => (id === null ? NAMES[SYSTEM_EDITOR] : NAMES[id] ?? id);

/** What Open in editor offers, the default first: every available editor, then
 *  the system opener. A default this machine lacks is skipped, not offered; no
 *  default is the system opener's. */
export function editorChoices(e: Editors): string[] {
  const all = [...e.available, ...(e.system ? [SYSTEM_EDITOR] : [])];
  const first = e.default ?? SYSTEM_EDITOR;
  return all.includes(first) ? [first, ...all.filter((x) => x !== first)] : all;
}

/** The answer, or the server's reason it has none (a remote client is refused).
 *  Asked only when `ask`: a document with no file to open needs no editor. */
export function useEditors(ask = true): Editors | string | null {
  const [editors, setEditors] = useState<Editors | string | null>(null);
  useEffect(() => {
    if (!ask) return;
    request<Editors>("/editors").then(
      (r) => setEditors(r.status === 200 && Array.isArray(r.body?.available) ? r.body : r.status === 200 ? "No editor found on this machine" : detailOf(r.body)),
      () => setEditors("Could not ask the server for its editors"),
    );
  }, [ask]);
  return editors;
}
