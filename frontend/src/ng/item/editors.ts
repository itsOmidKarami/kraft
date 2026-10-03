import { useEffect, useState } from "react";
import { detailOf, request } from "../http";

/** `GET /editors`: the editors whose executable this machine has, whether the
 *  system opener is there, and the default chosen in Settings › Appearance. */
export type Editors = { available: string[]; system: boolean; default: string | null };

const NAMES: Record<string, string> = { code: "VS Code", cursor: "Cursor", zed: "Zed", obsidian: "Obsidian" };
/** An editor's name; null is the system's default app. */
export const editorName = (id: string | null) => (id === null ? "System default" : NAMES[id] ?? id);

/** What Open in editor offers, the default first: every available editor, then
 *  the system opener. A default this machine lacks is skipped, not offered. */
export function editorChoices(e: Editors): (string | null)[] {
  const all: (string | null)[] = [...e.available, ...(e.system ? [null] : [])];
  return all.includes(e.default) ? [e.default, ...all.filter((x) => x !== e.default)] : all;
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
