import type { Finding, WorkItemDetail } from "./api";
import { rightUri } from "./review";

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

// Grouped by the diff's right-side URI only: that copy shows on the diff's lines, and a second
// copy on the worktree's file path would list every finding twice in Problems.
export function findingsByUri(id: string, detail: WorkItemDetail): Map<string, LocatedFinding[]> {
  const byUri = new Map<string, LocatedFinding[]>();
  for (const f of findingsOf(detail).located) {
    const uri = rightUri(id, f.file);
    byUri.set(uri, [...(byUri.get(uri) ?? []), f]);
  }
  return byUri;
}

// Per severity, for the diff's line marks: every finding gets one, whatever its squiggle shows.
export function marksBySeverity(findings: LocatedFinding[] = []): Record<Severity, LocatedFinding[]> {
  const marks: Record<Severity, LocatedFinding[]> = { error: [], warning: [], information: [] };
  for (const f of findings) marks[f.severity].push(f);
  return marks;
}

export const showsFindings = (d: WorkItemDetail) => Boolean(d.pending_gate) || d.status === "paused";
