import { agentsIn, elapsed, tokens } from "../../format";
import type { LogLine, Subagent } from "../../types";

/** What a sub-agent's prompt says when the part of the log read does not hold it. */
export const PROMPT_NOT_SHOWN = "This sub-agent's prompt is before the part of the log shown.";

/** The words for a sub-agent's status. */
export const STATUS_WORDS: Record<Subagent["status"], string> = { running: "running", completed: "done", failed: "failed", killed: "stopped", cut_off: "cut off" };

/** What a list of sub-agents comes to, for the Overview's count line and the fact row. */
export function subagentCount(list: Subagent[]): { running: number; finished: number; cutOff: number } {
  const running = list.filter((a) => a.status === "running").length;
  const cutOff = list.filter((a) => a.status === "cut_off").length;
  return { running, finished: list.length - running - cutOff, cutOff };
}

/** How many of each session's sub-agents run, by session id: the count a task's box on the graph shows. */
export const runningCounts = (bySession: Record<string, Subagent[]>): Record<string, number> =>
  Object.fromEntries(Object.entries(bySession).map(([id, l]) => [id, subagentCount(l).running]));

/** "2 running · 3 finished · 1 cut off", leaving out what is zero. */
export function countLine(list: Subagent[]): string {
  const c = subagentCount(list);
  return [c.running && `${c.running} running`, c.finished && `${c.finished} finished`, c.cutOff && `${c.cutOff} cut off`].filter(Boolean).join(" · ");
}

/** The dot a sub-agent's status draws (the pill dots of the attempt menu). */
export const statusTone = (status: string) => (status === "running" ? "is-live" : status === "completed" ? "is-ok" : "is-bad");

/** One line of what a sub-agent used: type, status, tokens, tool uses, time. */
export const subagentMeta = (a: Subagent) => [
  a.type,
  STATUS_WORDS[a.status],
  a.tokens != null ? `${tokens(a.tokens)} tokens` : null,
  a.tool_uses != null ? `${a.tool_uses} tool use${a.tool_uses === 1 ? "" : "s"}` : null,
  a.duration_ms != null ? elapsed(a.duration_ms) : null,
].filter(Boolean).join(" · ");

/** What a sub-agent last did while it runs, else the first line of its answer. */
export const subagentSaid = (a: Subagent) => (a.status === "running" ? (a.last_tool ? `running ${a.last_tool}` : "") : (a.summary ?? "").split("\n")[0]);

export type AgentEntry = ReturnType<typeof agentsIn>[number] & { hint?: string };

/** The sub-agents a log picker offers: the answer's list when it has one (tokens and time beside the type), else the
 *  ones the loaded rows name. */
export function agentEntries(subagents: Subagent[] | null, lines: LogLine[]): AgentEntry[] {
  if (!subagents?.length) return agentsIn(lines);
  return subagents.map((a): AgentEntry => ({
    id: a.id,
    label: a.description,
    type: a.type ?? undefined,
    status: a.status,
    hint: [a.type, a.tokens != null ? `${tokens(a.tokens)} tokens` : null, a.duration_ms != null ? elapsed(a.duration_ms) : null].filter(Boolean).join(" · ") || undefined,
  }));
}
