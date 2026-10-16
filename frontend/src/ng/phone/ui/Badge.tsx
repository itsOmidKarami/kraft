import type { ReactNode } from "react";
import "./ui.css";

export type BadgeTone = "neutral" | "info" | "warn" | "bad" | "ok" | "muted" | "change";

/** A read-only label: a status, a kind, a count. A thing you press is a Chip. */
export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`ph-badge-tag ph-tone-${tone}`}>{children}</span>;
}
