/** A served document as the editor shows it: a truncated one says so at the
 *  end, so nobody reviews the first half thinking it is the whole. */
export const docText = (content: string, truncated?: boolean): string =>
  truncated ? `${content}\n\n---\n*Truncated: open it in the web UI for the whole document.*\n` : content;

const segment = (title: string) => title.replace(/[\\/]/g, "-");

/** A virtual document's path: `/<key>/<title>.md`, the title's slashes made
 *  dashes so it stays one segment (the tab shows it), the key left as given. */
export const docPath = (key: string, title: string): string => `/${key}/${segment(title)}.md`;

/** A gate document's file name: the title, its slashes and parentheses made
 *  dashes, then `(<id>).md`. The markdown preview's tab label ends with it,
 *  which is how previewItemId finds the item a preview shows. */
export const artifactBasename = (id: string, title: string): string => `${segment(title).replace(/[()]/g, "-")} (${id}).md`;
