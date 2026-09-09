export type DiffLine = { tag: " " | "+" | "-"; text: string };

/**
 * A line diff between two strings the caller already holds.
 *
 * No dependency: `package.json` has no diff library, and one is not worth
 * adding for two textareas. No server route either -- `difflib.unified_diff`
 * behind a new endpoint would be a round trip per toggle to diff two strings
 * that are already in the browser, plus a route and a client function to test.
 *
 * Context lines are returned too (tag `" "`), so the output renders as a diff
 * and not as a pile of changes with no location. Identical inputs produce all
 * context and no `+`/`-`.
 *
 * ponytail: O(n·m) LCS table; these are <=8 KB steering files and a 10-node
 * template -- switch to Myers if a real document ever lands in one of these
 * editors.
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");

  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:].
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ tag: " ", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ tag: "-", text: a[i++] });
    } else {
      out.push({ tag: "+", text: b[j++] });
    }
  }
  while (i < a.length) out.push({ tag: "-", text: a[i++] });
  while (j < b.length) out.push({ tag: "+", text: b[j++] });
  return out;
}
