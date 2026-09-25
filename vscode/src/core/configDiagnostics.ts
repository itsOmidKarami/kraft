import type { ConfigIssue, Position } from "./api";

export interface Diag { line: number; column: number; message: string; related?: Position }

const at = (i: ConfigIssue): Diag => ({
  line: Math.max(0, i.line - 1),
  column: Math.max(0, i.column - 1),
  message: i.message,
  ...(i.related ? { related: i.related } : {}),
});

export function fromCheck(issues: ConfigIssue[], editedFile: string): Diag[] {
  return issues.map((i) => (i.file === editedFile ? at(i) : { line: 0, column: 0, message: `${i.chain ?? i.file}: ${i.message}` }));
}

export function fromLint(issues: ConfigIssue[]): Map<string, Diag[]> {
  const out = new Map<string, Diag[]>();
  for (const i of issues) out.set(i.file, [...(out.get(i.file) ?? []), at(i)]);
  return out;
}

export function mergeLint(previous: Set<string>, lint: Map<string, Diag[]>, dirty: Set<string>) {
  const set = new Map([...lint].filter(([file]) => !dirty.has(file)));
  const clear = [...previous].filter((f) => !lint.has(f) && !dirty.has(f));
  return { set, clear };
}
