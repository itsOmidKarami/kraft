import type { WorkItemDetail } from "./api";

export interface DraftComment { file: string; line: number; body: string; side?: "base" }
export interface Draft { comments: DraftComment[]; summary?: string }
export type Destination = { kind: "reject"; gate: string } | { kind: "resume" } | { kind: "none"; reason: string };

const indent = (body: string) => body.trim().split("\n").join("\n  ");

export function composeNote(draft: Draft): string {
  const lines = [...draft.comments]
    .sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line)
    .map((c) => `- ${c.file}:${c.line}${c.side === "base" ? " (base)" : ""} — ${indent(c.body)}`);
  if (draft.summary?.trim()) lines.push(`- (general) — ${indent(draft.summary)}`);
  return ["Review comments:", ...lines].join("\n");
}

export function destinationOf(detail: WorkItemDetail | undefined): Destination {
  if (!detail) return { kind: "none", reason: "Kraft has not loaded this item yet." };
  if (detail.pending_gate) return { kind: "reject", gate: detail.pending_gate };
  if (detail.status === "paused" && detail.steerable) return { kind: "resume" };
  if (detail.status === "paused") return { kind: "none", reason: "This paused item takes no steer right now." };
  return { kind: "none", reason: "Comments can be sent at a gate (as a reject) or to a paused item (as a steer)." };
}
