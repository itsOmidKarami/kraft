import { DEFAULT_VIEW } from "./fixture.default";
import type { Authored, DraftView } from "./types";

/** The shipped default chain's tail, after the trimmed fixture's six nodes: the final review gate, `mark_ready`,
 *  then `merge`. */
const FINAL = { id: "final_review", kind: "gate", chain_finalized: true, message: "Review the complete work item.", reject_to: "implementation" };
const READY = { id: "mark_ready", kind: "exec", tasks: [{ id: "ready", kind: "forge", target: "mr.mark_ready" }] };
const POLICY = { restart_from: "merge_request_feedback" };
const REPAIR = { id: "repair", kind: "agent", harness: "claude", prompt: "Fix the branch the merge queue dropped." };
/** A recovery for the merge itself: it waits and tries again. Read only on the plan, as `add_handler` starts one
 *  there: a `tasks` shorthand's `main` step is no path a step's `read_only` can be set on. */
const recoveryOf = (readOnly: boolean) => ({ tasks: [{ id: "wait", kind: "subprocess", command: "sleep 60" }], ...(readOnly ? { read_only: true } : {}) });

/** The default chain with its final review, `mark_ready` and its `merge` node, whose task merges the merge request. With
 *  `policy`, `merge` declares `on_merge_rejected`: restart from `merge_request_feedback`, its repair one agent
 *  task. With `recovery`, `merge` also has an `on_failure`, read only unless `"writes"`. With `tail`, the chain is those three nodes alone,
 *  so no exec node comes before the final gate. Tests only. */
export function mergeView({ policy = true, recovery = false, tail = false }: { policy?: boolean; recovery?: boolean | "writes"; tail?: boolean } = {}): DraftView {
  const base = DEFAULT_VIEW.result;
  const RECOVERY = recoveryOf(recovery === true);
  const file = "chains/default.yaml";
  const authored: Authored = {
    id: "merge",
    kind: "exec",
    steps: [{ id: "main", tasks: [{ id: "merge", extends: "merge_mr" }] }],
    ...(policy ? { on_merge_rejected: { ...POLICY, repair: { steps: [{ id: "main", tasks: [{ id: "repair", extends: "repair_merge_queue_failure" }] }] } } } : {}),
    ...(recovery ? { on_failure: RECOVERY } : {}),
  };
  const resolved: Authored = {
    id: "merge",
    kind: "exec",
    tasks: [{ id: "merge", kind: "forge", target: "mr.merge" }],
    ...(policy ? { on_merge_rejected: { ...POLICY, repair: { tasks: [REPAIR] } } } : {}),
    ...(recovery ? { on_failure: RECOVERY } : {}),
  };
  const chain = base.resolved!.chain;
  const head = (nodes: unknown) => (tail ? [] : (nodes as Authored[]));
  return {
    ...DEFAULT_VIEW,
    result: {
      ...base,
      model: { [file]: { ...base.model[file], nodes: [...head(base.model[file].nodes), FINAL, READY, authored] } },
      resolved: {
        ...base.resolved!,
        chain: { ...chain, nodes: [...head(chain.nodes), FINAL, READY, resolved] },
        task_paths: [...base.resolved!.task_paths, "mark_ready.main.ready", "merge.main.merge", ...(policy ? ["merge.on_merge_rejected.repair.main.repair"] : [])],
        nodes: [...base.resolved!.nodes, { id: "final_review", kind: "gate", icon: null }, { id: "mark_ready", kind: "exec", icon: null }, { id: "merge", kind: "exec", icon: null }],
      },
      sources: {
        ...base.sources,
        merge: {
          id: { value: "merge", source: "chain" },
          kind: { value: "exec", source: "chain" },
          on_base_changed: { value: null, source: "default" },
          on_merge_rejected: policy ? { value: POLICY, source: "chain" } : { value: null, source: "default" },
          skippable: { value: true, source: "default" },
        },
        ...(recovery ? { "merge.on_failure": { read_only: recovery === true ? { value: true, source: "chain" } : { value: false, source: "default" } } } : {}),
      },
    },
  };
}
