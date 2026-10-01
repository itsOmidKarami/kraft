import { fieldMeta, type FieldKind } from "../../templates/fields";

export type DraftField = { group: "task_config" | "policy"; key: string; label: string; kind: FieldKind; hint?: string };

const field = (group: DraftField["group"], key: string, hint?: string): DraftField => {
  const m = fieldMeta(group === "policy" ? `policy.${key}` : key);
  return { group, key, label: m.label, kind: m.kind, hint };
};

/** What ✎ offers on a node or step: policy only, `task_config` lives on tasks (Decided 8). */
export const POLICY: DraftField[] = ["time_cap_minutes", "total_time_cap_minutes", "budget_usd", "token_budget"].map((k) => field("policy", k));

/** On a task, its own fields first. The item API does not say a task's kind, so
 *  the agent and subprocess fields are all offered and the server refuses the
 *  one the task lacks, as the op's problem (Decided 8, amended). */
export const TASK: DraftField[] = [field("task_config", "harness", "agent tasks"), field("task_config", "model", "agent tasks"), field("task_config", "effort", "agent tasks"), field("task_config", "prompt", "agent tasks"), field("task_config", "command", "subprocess tasks"), ...POLICY];

export const fieldsFor = (path: string): DraftField[] => (path.split(".").length === 3 ? TASK : POLICY);
