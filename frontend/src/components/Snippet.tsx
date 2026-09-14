import { Fragment } from "react";
import { plainMarkdown } from "../format";

// FTS5 snippet() wraps matches in literal [ ] — see the 4A backend's
// snippet(documents_fts, 1, '[', ']', '…', 64) config. Split on a balanced
// [ ... ] pair; everything else is plain text (never dangerouslySetInnerHTML).
const PART = /\[([^\]]+)\]/g;

// Snippets are cut from raw markdown (W5.3); the stripping lives in format.ts
// with docTitle (W11 · H), which reads a body's opening line the same way.
export { plainMarkdown };

export function Snippet({ text: raw }: { text: string }) {
  const text = plainMarkdown(raw);
  const nodes: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(PART)) {
    const i = m.index ?? 0;
    if (i > last) nodes.push(text.slice(last, i));
    nodes.push(<mark key={i}>{m[1]}</mark>);
    last = i + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return (
    <>
      {nodes.map((n, i) => (
        <Fragment key={i}>{n}</Fragment>
      ))}
    </>
  );
}
