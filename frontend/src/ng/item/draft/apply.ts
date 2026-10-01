import { detailOf } from "../../http";
import { showToast } from "../../ui/Toast";
import { useDraft } from "./context";
import type { Refusal } from "./types";

export type Outcome = { kind: "applied" } | { kind: "moved"; passed: number } | { kind: "refused" } | { kind: "gone" };

/** Apply the draft and say what happened (Decided 10). `moved` and `refused` leave the person where they are, to read why; the rest are over. */
export function useApply() {
  const d = useDraft()!;
  return async (): Promise<Outcome> => {
    const adds = d.ops.flatMap((o) => (o.op === "add_node" && !o.passed ? [o] : []));
    const n = d.changes;
    const a = await d.draft.apply();
    if (a.status === 200) {
      showToast(`Applied ${n} ${n === 1 ? "change" : "changes"} · ${adds.length ? `the run reaches ${adds.map((x) => x.node.id).join(", ")} after ${adds[0].after}` : "they apply as the run reaches each node"}`, 6000);
      d.reload();
      return { kind: "applied" };
    }
    const body = a.body as Refusal;
    if (a.status === 409 && Array.isArray(body.passed)) return { kind: "moved", passed: body.passed.length };
    if (a.status === 422) return { kind: "refused" };
    showToast(a.status === 404 ? "That draft is gone" : detailOf(body), 6000);
    d.reload();
    return { kind: "gone" };
  };
}
