import { deriveState, type ItemDisplayState } from "../../../frontend/src/deriveState";
import { STATUS_GROUPS } from "../../../frontend/src/statusGroups";
import type { WorkItem } from "./api";

export type Action = "pause" | "resume" | "retry" | "cancel" | "skip" | "escalate" | "archive";

const ACTIONS: Record<ItemDisplayState, Action[]> = {
  running: ["pause", "cancel"],
  rate_limited: ["pause", "cancel"],
  waiting: ["pause", "cancel"],
  escalating: ["cancel"],
  paused: ["resume", "cancel"],
  not_started: ["resume", "cancel"],
  gate: ["skip", "cancel"],
  capped: ["retry", "skip", "escalate", "cancel"],
  question: ["retry", "skip", "escalate", "cancel"],
  budget: ["retry", "skip", "escalate", "cancel"],
  escalated: ["retry", "skip", "escalate", "cancel"],
  done: ["archive"],
  abandoned: ["archive"],
  archived: [],
};

export function groupItems(items: WorkItem[], repos: string[] | "all") {
  const shown = items.filter((i) => !i.archived_at && (repos === "all" || repos.includes(i.repo)));
  return STATUS_GROUPS.map((g) => ({
    id: g.id,
    label: g.label,
    items: shown.filter((i) => g.test(deriveState(i))),
  })).filter((g) => g.items.length > 0);
}

export const actionsFor = (item: WorkItem): Action[] => ACTIONS[deriveState(item).state] ?? [];

export const needsYouCount = (items: WorkItem[]) =>
  items.filter((i) => !i.archived_at && deriveState(i).needsYou).length;

export function describe(item: WorkItem): string {
  const parts = [item.id];
  if (item.current_node_id) parts.push(item.current_node_id);
  const p = (item as { progress?: { current?: number; total?: number } | null }).progress;
  if (p?.current && p?.total) parts.push(`Task ${p.current} of ${p.total}`);
  return parts.join(" · ");
}
