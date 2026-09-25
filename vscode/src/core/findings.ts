import type { Finding, WorkItemDetail } from "./api";

export type Severity = "error" | "warning" | "information";
export interface LocatedFinding { file: string; line: number; severity: Severity; message: string; source: string }

const SEVERITY: Record<string, Severity> = { critical: "error", important: "warning" };

export function findingsOf(detail: WorkItemDetail) {
  const all: Finding[] = [...(detail.deferred_findings ?? []), ...(detail.judge_stop_note ?? []).flatMap((n) => n.findings ?? [])];
  const located: LocatedFinding[] = [];
  const unlocated: Finding[] = [];
  for (const f of all) {
    if (f.file && f.line !== null && f.line !== undefined) {
      located.push({ file: f.file, line: Math.max(0, f.line - 1), severity: SEVERITY[f.severity] ?? "information", message: f.message, source: `Kraft · ${f.source_plugin}` });
    } else {
      unlocated.push(f);
    }
  }
  return { located, unlocated };
}

export const showsFindings = (d: WorkItemDetail) => Boolean(d.pending_gate) || d.status === "paused";
