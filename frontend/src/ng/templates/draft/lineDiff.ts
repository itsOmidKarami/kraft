/** Line diffs for the review's YAML diff and the YAML view's gutter (the
 *  prototype's `lineDiff` and `gutter`). Display only: the server owns the files. */
export type DiffOp = { t: " " | "+" | "-"; s: string; bi?: number };

/** A longest-common-subsequence diff of two line lists. */
export function lineDiff(a: string[], b: string[]): DiffOp[] {
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const ops: DiffOp[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ t: " ", s: b[j], bi: j }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) ops.push({ t: "-", s: a[i++] });
    else { ops.push({ t: "+", s: b[j], bi: j }); j++; }
  }
  while (i < n) ops.push({ t: "-", s: a[i++] });
  while (j < m) { ops.push({ t: "+", s: b[j], bi: j }); j++; }
  return ops;
}

/** The diff with unchanged runs folded to one `⋯`, keeping `context` lines around each change. */
export function folded(ops: DiffOp[], context = 2): (DiffOp | { t: "…" })[] {
  const keep = ops.map((o, i) => o.t !== " " || ops.slice(Math.max(0, i - context), i + context + 1).some((q) => q.t !== " "));
  const out: (DiffOp | { t: "…" })[] = [];
  ops.forEach((o, i) => {
    if (keep[i]) out.push(o);
    else if (out[out.length - 1]?.t !== "…") out.push({ t: "…" });
  });
  return out;
}

/** Per line of `text`, `+` (added) or `~` (changed) against the published text; a new file is all `+`. */
export function gutter(published: string | null, text: string): string[] {
  const b = text.split("\n");
  if (published == null) return b.map((s) => (s.trim() ? "+" : ""));
  const marks = b.map(() => "");
  const ops = lineDiff(published.split("\n"), b);
  ops.forEach((o, k) => {
    if (o.t !== "+" || o.bi == null) return;
    marks[o.bi] = ops[k - 1]?.t === "-" || ops[k + 1]?.t === "-" ? "~" : "+";
  });
  return marks;
}
