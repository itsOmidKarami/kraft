/** What previewItemId reads off a tab: its label, and a webview tab's viewType. */
export type TabLike = { label: string; viewType?: string };

/** The item a markdown preview tab shows, when it is a gate document Kraft
 *  opened this session: the id in the label's last `(<id>).md` (artifactBasename
 *  makes the title's own parentheses dashes). Any other tab, or an id Kraft
 *  did not open, is undefined, so no Approve binds to a render it never showed. */
export function previewItemId(tab: TabLike | undefined, opened: ReadonlySet<string>): string | undefined {
  if (!tab?.viewType?.endsWith("markdown.preview")) return undefined;
  const id = [...tab.label.matchAll(/\(([^()]+)\)\.md/g)].at(-1)?.[1];
  return id !== undefined && opened.has(id) ? id : undefined;
}
