import { useState } from "react";
import { useProviders } from "../../harnesses/useProviders";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import type { ChainNode, Policy } from "../../../types";
import { act } from "../actions";
import { agentTasks, attemptsAt, effortOf, effortOptions, materialized, modelOf, modelSuggestions, oneOf, providersOf, type Given } from "../chainValues";
import type { ItemDetail } from "../useItem";
import { useRepoEntry } from "../useRepoEntry";
import { OverrideRow } from "./OverrideRow";

type Agent = NonNullable<ItemDetail["agent_overrides"]>;
type NodeFields = NonNullable<ItemDetail["node_overrides"]>[string];

/** The harness choices and the providers' lists, for the rows below. */
function useChoices(item: ItemDetail, node?: string) {
  const opts = useHarnessOptions();
  const listed = useProviders();
  const repo = useRepoEntry(item.repo);
  const chain = materialized(item);
  const h = typeof opts === "string" ? null : opts.harnesses;
  const tasks = chain ? agentTasks(chain, node) : [];
  const providers = chain ? providersOf(chain, h, node) : [];
  return {
    tasks,
    model: oneOf(tasks.map((t) => modelOf(t, h, repo))),
    effort: oneOf(tasks.map((t) => effortOf(t, h))),
    models: modelSuggestions(providers, listed, h),
    efforts: effortOptions(providers, listed),
  };
}

/** Sends one field's PATCH and keeps the server's refusal on that field's row. */
function useSave(item: ItemDetail, reload: () => void) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const send = async (key: string, body: Record<string, unknown>) => {
    setErrors(({ [key]: _gone, ...rest }) => rest);
    const r = await act.patch(item.id, body);
    if (!r.ok) setErrors((e) => ({ ...e, [key]: r.error }));
    reload();
  };
  return { errors, send };
}

/** The chain pane's model and effort for every agent task of an item that has
 *  not started (`agent_overrides`; `kraft item set-overrides`). Saved at once,
 *  one field per PATCH: the server merges fields, and `null` drops one. */
export function ItemAgentRows({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const c = useChoices(item);
  const { errors, send } = useSave(item, reload);
  const own: Agent = item.agent_overrides ?? {};
  if (!c.tasks.length) return null;
  const put = (key: keyof Agent, value: unknown) => void send(key, { agent_overrides: { [key]: value ?? null } });
  return (
    <>
      <h3 className="ip-h">Every agent task</h3>
      <div className="cfg">
        <OverrideRow label="model" kind="text" own={own.model ?? undefined} given={c.model} suggest={c.models} problem={errors.model} onSave={(v) => put("model", v)} onReset={() => put("model", undefined)} />
        <OverrideRow label="effort" kind="enum" own={own.effort ?? undefined} given={c.effort} options={c.efforts} problem={errors.effort} onSave={(v) => put("effort", v)} onReset={() => put("effort", undefined)} />
      </div>
      <p className="item-muted">Saved at once. A node's own setting wins over this one.</p>
    </>
  );
}

/** An exec node's own settings on an item that has not started
 *  (`node_overrides`; `kraft item set-node-override`): its agents' model and
 *  effort, its fix loop's attempts, whether it escalates on its own when
 *  stuck, and a note added to its agents' prompts. Saved at once, one field
 *  per PATCH. */
export function NodeOverrideRows({ item, node, policy, reload }: { item: ItemDetail; node: ChainNode; policy: Policy | null; reload: () => void }) {
  const c = useChoices(item, node.id);
  const { errors, send } = useSave(item, reload);
  const own: NodeFields = item.node_overrides?.[node.id] ?? {};
  const chain = materialized(item);
  const loop = chain?.chain.nodes.find((n) => n.id === node.id)?.fix_loop;
  const put = (key: keyof NodeFields, value: unknown) => void send(key, { node_overrides: { [node.id]: { [key]: value ?? null } } });
  const row = (key: keyof NodeFields, props: Omit<React.ComponentProps<typeof OverrideRow>, "own" | "onSave" | "onReset" | "problem">) => (
    <OverrideRow key={key} {...props} own={own[key] ?? undefined} problem={errors[key]} onSave={(v) => put(key, v)} onReset={() => put(key, undefined)} />
  );
  const all = item.agent_overrides ?? {};
  const wide = (v: string | null | undefined, chainValue: Given): Given => (v ? { value: v, source: "item-wide" } : chainValue);
  const attempts = chain && (loop || node.fix_loop) ? attemptsAt(chain, node.id, item.policy_override) ?? (policy?.default?.attempts != null ? { value: String(policy.default.attempts), source: "policy" } : null) : null;
  const rows = [
    ...(c.tasks.length ? [
      row("model", { label: "model", kind: "text", given: wide(all.model, c.model), suggest: c.models }),
      row("effort", { label: "effort", kind: "enum", given: wide(all.effort, c.effort), options: c.efforts }),
    ] : []),
    ...(loop || node.fix_loop ? [row("attempts", { label: "fix loop attempts", kind: "int", given: attempts })] : []),
    row("auto_escalate_stuck", { label: "auto-escalate", kind: "bool", given: policy ? { value: (policy.auto_escalate_stuck ?? true) ? "on" : "off", source: "policy" } : null }),
    ...(c.tasks.length ? [row("extra_prompt", { label: "extra prompt", kind: "long", given: { value: "none", source: "chain" }, placeholder: "Added after each agent task's own prompt in this node" })] : []),
  ];
  return (
    <>
      <h3 className="ip-h">This node, for this item</h3>
      <div className="cfg">{rows}</div>
      <p className="item-muted">Saved at once. Locked once the node starts.</p>
    </>
  );
}
