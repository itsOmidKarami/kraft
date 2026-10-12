/** What previewOf reads off a tab: its label, and a webview tab's viewType. */
export type TabLike = { label: string; viewType?: string };

/** A gate document's preview: the item, and the basename that render used. */
export type ShownPreview = { id: string; basename: string };

/** The gate document a markdown preview tab shows, when Kraft opened it: the
 *  label ends with " " + a basename Kraft previewed (`opened`: basename → id),
 *  whatever the localized prefix (`Preview`, `[Preview]`, `Vorschau`). Any
 *  other tab, or another file whose name merely ends `(<id>).md`, is undefined,
 *  so no Approve binds to a render Kraft never showed. */
export function previewOf(tab: TabLike | undefined, opened: ReadonlyMap<string, string>): ShownPreview | undefined {
  if (!tab?.viewType?.endsWith("markdown.preview")) return undefined;
  let found: ShownPreview | undefined;
  for (const [basename, id] of opened)
    if (tab.label.endsWith(` ${basename}`) && basename.length > (found?.basename.length ?? -1)) found = { id, basename };
  return found;
}

/** Whether a preview shows an older render of its item than the latest one
 *  Kraft showed (`latest`: id → basename), e.g. the spec's tab still open once
 *  the plan gate's document is up. Approving there would send the newer
 *  render's digest, so it reads first instead. */
export const previewIsStale = (p: ShownPreview, latest: ReadonlyMap<string, string>): boolean => latest.get(p.id) !== p.basename;
