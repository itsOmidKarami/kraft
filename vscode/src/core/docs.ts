/** A served document as the editor shows it: a truncated one says so at the
 *  end, so nobody reviews the first half thinking it is the whole. */
export const docText = (content: string, truncated?: boolean): string =>
  truncated ? `${content}\n\n---\n*Truncated: open it in the web UI for the whole document.*\n` : content;

/** A virtual document's path: `/<key>/<title>.md`, the title's slashes made
 *  dashes so it stays one segment (the tab shows it), the key left as given. */
export const docPath = (key: string, title: string): string => `/${key}/${title.replace(/[\\/]/g, "-")}.md`;
