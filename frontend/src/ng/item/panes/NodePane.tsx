import { tokenText, tokenTip, usd } from "../../../format";
import type { ChainNode } from "../../../types";
import { stepsOf, taskName } from "../paths";
import { loopRounds, sessionsOf } from "../nodeGraph";
import type { ItemDetail } from "../useItem";

/** The node pane's Overview (Decisions §5): progress, fix loop, on failure,
 *  then, its numbers (GAP §2 #17), and its steps, each selecting itself. */
export function NodeOverview({ item, node, onStep, onNode }: { item: ItemDetail; node: ChainNode; onStep: (step: string) => void; onNode: (node: string) => void }) {
  const { steps } = stepsOf(node);
  const paths = steps.flatMap((s) => s.tasks);
  const done = paths.filter((p) => sessionsOf(item, p).at(-1)?.status.startsWith("done")).length;
  const nodes = item.chain_definition.nodes;
  const next = nodes[nodes.findIndex((n) => n.id === node.id) + 1];
  const rounds = loopRounds(item, node);
  const by = item.usage?.by_node.find((r) => r.node === node.id);
  return (
    <>
      <dl className="item-facts ip-facts">
        {paths.length > 0 && <div><dt>progress</dt><dd>{done} of {paths.length} tasks · {steps.length} {steps.length === 1 ? "step" : "steps"}</dd></div>}
        {node.fix_loop && <div><dt>fix loop</dt><dd>{rounds ? `round ${rounds.latest}${rounds.total ? ` of ${rounds.total}` : ""}` : "not looped"}</dd></div>}
        {node.on_failure?.length ? <div><dt>on failure</dt><dd>{node.on_failure.map(taskName).join(", ")}</dd></div> : null}
        {next && <div><dt>then</dt><dd><button type="button" className="item-link is-strong is-mono" onClick={() => onNode(next.id)}>{next.id}</button></dd></div>}
        {by && <div><dt>ran</dt><dd>{by.sessions} {by.sessions === 1 ? "session" : "sessions"} · <span data-tip={tokenTip(by)}>{tokenText(by)} tokens</span> · {usd(by.cost_usd, by.cost_complete)}</dd></div>}
      </dl>
      {steps.length > 0 && (
        <>
          <h3 className="ip-h">Steps</h3>
          <ul className="ip-list">
            {steps.map((s) => {
              const last = s.tasks.map((p) => sessionsOf(item, p).at(-1));
              const mark = last.every((x) => x?.status.startsWith("done")) ? "✓" : last.some((x) => x && ["running", "pending", "paused"].includes(x.status)) ? "●" : "○";
              return (
                <li key={s.id}>
                  <button type="button" className="ip-row" onClick={() => onStep(s.id)}>
                    <span className={`ip-mark${mark === "●" ? " is-live" : ""}`} aria-hidden>{mark}</span>
                    <span className="is-mono">{s.id}</span>
                    <span className="ip-row-meta">{s.tasks.length === 1 ? taskName(s.tasks[0]) : `${s.tasks.length} tasks`}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </>
  );
}

/** A node's values as the item froze them, read-only (✎ is the item draft's, W11). */
export function NodeConfig({ item, node, onReset, controls }: { item: ItemDetail; node: ChainNode; onReset: () => void; controls?: boolean }) {
  // With `controls` the node's overrides have rows of their own, each with its reset.
  const o = controls ? undefined : item.node_overrides?.[node.id];
  const rows: [string, string | null | undefined][] = [
    ["kind", node.kind ?? "exec"],
    ["fix loop", node.fix_loop],
    ["reject to", node.reject_to],
    ["rebase back to", node.rebase_bounce_to],
    ["auto-escalate", node.auto_escalate == null ? null : node.auto_escalate ? "on" : "off"],
    ["on failure", node.on_failure?.map(taskName).join(", ")],
  ];
  return (
    <>
      <h3 className="ip-h">As frozen at intake</h3>
      <dl className="item-facts ip-facts">
        {rows.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="is-mono">{v}</dd></div>)}
      </dl>
      {o && Object.keys(o).length > 0 && (
        <>
          <h3 className="ip-h">Changed for this item</h3>
          <p className="ip-override"><span className="is-mono">{Object.entries(o).map(([k, v]) => `${k} ${v}`).join(", ")}</span> <button type="button" className="item-link" onClick={onReset}>reset</button></p>
        </>
      )}
    </>
  );
}
