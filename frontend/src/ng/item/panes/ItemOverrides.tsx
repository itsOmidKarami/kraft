import { useState } from "react";
import { useProviders } from "../../harnesses/useProviders";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import type { ChainNode, Policy } from "../../../types";
import { act } from "../actions";
import { agentTasks, effortOf, effortOptions, materialized, modelOf, modelSuggestions, nodeAt, oneOf, providersOf } from "../chainValues";
import type { ItemDetail } from "../useItem";
import { OverrideRow } from "./OverrideRow";

type Agent = NonNullable<ItemDetail["agent_overrides"]>;
type NodeFields = NonNullable<ItemDetail["node_overrides"]>[string];

/** The harness choices and the providers' lists, for the rows below. */
function useChoices(item: ItemDetail, node?: string) {
  const opts = useHarnessOptions();
  const listed = useProviders();
  const chain = materialized(item);
  const h = typeof opts === "string" ? null : opts.harnesses;
  const tasks = chain ? agentTasks(chain, node) : [];
  const providers = chain ? providersOf(chain, h, node) : [];
  return {
    tasks,
    model: oneOf(tasks.map((t) => modelOf(t, h))),
    effort: oneOf(tasks.map((t) => effortOf(t, h))),
    models: modelSuggestions(providers, listed, h),
    efforts: effortOptions(providers, listed),
  };
}

/** Saves one PATCH at a time and keeps the server's refusal in view. */
function useSave(item: ItemDetail, reload: () => void) {
  const [error, setError] = useState<string | null>(null);
  const send = async (...bodies: Record<string, unknown>[]) => {
    setError(null);
    for (const body of bodies) {
      const r = await act.patch(item.id, body);
      if (!r.ok) {
        setError(r.error);
        break;
      }
    }
    reload();
  };
  return { error, send };
}

/** The chain pane's model and effort for every agent task of an item that has
 *  not started (`agent_overrides`; `kraft item set-overrides`). Saved at once:
 *  there is no run to stage them against. */
export function ItemAgentRows({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const c = useChoices(item);
  const { error, send } = useSave(item, reload);
  const own: Agent = item.agent_overrides ?? {};
  if (!c.tasks.length) return null;
  const put = (key: keyof Agent, value: unknown) => {
    const next: Record<string, unknown> = { ...own };
    if (value === undefined) delete next[key];
    else next[key] = value;
    void send({ agent_overrides: next });
  };
  return (
    <>
      <h3 className="ip-h">Every agent task</h3>
      <div className="cfg">
        <OverrideRow label="model" kind="text" own={own.model ?? undefined} inherited={c.model} suggest={c.models} onSave={(v) => put("model", v)} onReset={() => put("model", undefined)} />
        <OverrideRow label="effort" kind="enum" own={own.effort ?? undefined} inherited={c.effort} options={c.efforts} onSave={(v) => put("effort", v)} onReset={() => put("effort", undefined)} />
      </div>
      <p className="item-muted">Saved at once. A node's own setting wins over this one.</p>
      {error && <p className="item-error" role="alert">{error}</p>}
    </>
  );
}

/** An exec node's own settings on an item that has not started
 *  (`node_overrides`; `kraft item set-node-override`): its agents' model and
 *  effort, its fix loop's attempts, whether it escalates on its own when
 *  stuck, and a note added to its agents' prompts. Saved at once. */
export function NodeOverrideRows({ item, node, policy, reload }: { item: ItemDetail; node: ChainNode; policy: Policy | null; reload: () => void }) {
  const c = useChoices(item, node.id);
  const { error, send } = useSave(item, reload);
  const own: NodeFields = item.node_overrides?.[node.id] ?? {};
  const chain = materialized(item);
  const loop = chain ? nodeAt(chain, node.id)?.fix_loop : null;
  const put = (key: keyof NodeFields, value: unknown) => {
    if (value !== undefined) return void send({ node_overrides: { [node.id]: { [key]: value } } });
    // A node's fields merge, so dropping one is dropping the node's and setting the rest again.
    const { [key]: _gone, ...rest } = own;
    void send({ node_overrides: { [node.id]: {} } }, ...(Object.keys(rest).length ? [{ node_overrides: { [node.id]: rest } }] : []));
  };
  const row = (key: keyof NodeFields, props: Omit<React.ComponentProps<typeof OverrideRow>, "own" | "onSave" | "onReset">) => (
    <OverrideRow key={key} {...props} own={own[key] ?? undefined} onSave={(v) => put(key, v)} onReset={() => put(key, undefined)} />
  );
  const item_ = item.agent_overrides ?? {};
  const rows = [
    ...(c.tasks.length ? [
      row("model", { label: "model", kind: "text", inherited: item_.model ?? c.model, source: item_.model ? "item-wide" : undefined, suggest: c.models }),
      row("effort", { label: "effort", kind: "enum", inherited: item_.effort ?? c.effort, source: item_.effort ? "item-wide" : undefined, options: c.efforts }),
    ] : []),
    ...(loop || node.fix_loop ? [row("attempts", { label: "fix loop attempts", kind: "int", inherited: String(loop?.max_attempts ?? policy?.default?.attempts ?? "") || null })] : []),
    row("auto_escalate_stuck", { label: "auto-escalate", kind: "bool", inherited: policy ? ((policy.auto_escalate_stuck ?? true) ? "on" : "off") : null, source: "policy" }),
    ...(c.tasks.length ? [row("extra_prompt", { label: "extra prompt", kind: "long", inherited: "none", placeholder: "Added after each agent task's own prompt in this node" })] : []),
  ];
  if (!rows.length) return null;
  return (
    <>
      <h3 className="ip-h">This node, for this item</h3>
      <div className="cfg">{rows}</div>
      <p className="item-muted">Saved at once. Locked once the node starts.</p>
      {error && <p className="item-error" role="alert">{error}</p>}
    </>
  );
}
