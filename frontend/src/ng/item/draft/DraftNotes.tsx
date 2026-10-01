import { Button } from "../../ui/Button";
import { useDraft } from "./context";
import { lines, moveAfter, removeAt, removeNode } from "./view";

const opText = (ops: Parameters<typeof lines>[0], index: number) => lines(ops).filter((l) => l.index === index).map((l) => l.text).join("; ");

/** The draft's say in a pane (Decisions §5 Run passes an edit): each problem and
 *  each op the run has passed, red, with **Move after <current>** (an added node
 *  only) and **Remove**. Neither runs on its own (R18). With `node`, that node's
 *  own; without, all of them (the chain pane), since a passed added node has no
 *  node on the canvas. An added node's pane also says where it was added and can
 *  take it out of the draft. */
export function DraftNotes({ node }: { node?: string }) {
  const d = useDraft();
  if (!d || d.draft.status !== "ready") return null;
  const issues = d.issues.filter((i) => !node || i.node === node);
  const added = node ? d.ops.findIndex((o) => o.op === "add_node" && o.node.id === node && !o.passed) : -1;
  const current = d.raw.current_node_id;
  const send = (fn: Parameters<typeof d.draft.edit>[0], at: string) => void d.draft.edit(fn, at);
  return (
    <>
      {issues.map((i) => {
        const op = d.ops[i.index];
        return (
          <div key={`${i.index}-${i.message}`} className="idr-issue" role="group" aria-label={`Problem: ${opText(d.ops, i.index)}`}>
            <p className="idr-issue-op is-mono">{opText(d.ops, i.index)}</p>
            <p className="idr-issue-msg">{i.message}</p>
            <div className="idr-issue-actions">
              {i.passed && op?.op === "add_node" && current && <Button onClick={() => send((ops) => moveAfter(ops, i.index, current), i.node)}>Move after {current}</Button>}
              <Button onClick={() => send((ops) => removeAt(ops, i.index), i.node)}>Remove</Button>
            </div>
          </div>
        );
      })}
      {added >= 0 && node && (
        <div className="idr-added">
          <p className="idr-added-line">Added in this item's draft, after <span className="is-mono">{(d.ops[added] as { after: string }).after}</span>.</p>
          <Button onClick={() => send((ops) => removeNode(ops, node), node)}>Remove from the draft</Button>
        </div>
      )}
    </>
  );
}
