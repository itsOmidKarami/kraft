import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Seam } from "../../graph/layout";
import { showToast } from "../../ui/Toast";
import type { ItemDetail } from "../useItem";
import type { MarkedOp } from "./types";
import { useItemDraft, type ItemDraft } from "./useItemDraft";
import { count, editable as editableAt, issues, seams as seamsOf, type Issue } from "./view";

/** What the item page's draft pieces share: the draft hook's state and the
 *  item drawn with the draft applied. Without a provider (`useDraft()` is null)
 *  the page is W5's, byte for byte. */
export type DraftCtx = {
  draft: ItemDraft;
  /** The item as the server has it. */
  raw: ItemDetail;
  /** `raw` with the draft's chain when there is a draft, else `raw` itself. */
  shown: ItemDetail;
  ops: MarkedOp[];
  /** Lines Review shows (Decided 3). */
  changes: number;
  issues: Issue[];
  seams: Seam[];
  editable: (node: string) => boolean;
  reviewing: boolean;
  /** While Start waits on the draft: starts the item, which Review & apply
   *  runs after applying, or without applying when the person says so. */
  starting: (() => void) | null;
  /** Open or close Review & apply; `start` opens it as Start's question. */
  setReviewing: (on: boolean, start?: () => void) => void;
  reload: () => void;
};

const Ctx = createContext<DraftCtx | null>(null);
export const useDraft = () => useContext(Ctx);

export function ItemDraftProvider({ item, reload, children }: { item: ItemDetail; reload: () => void; children: ReactNode }) {
  const draft = useItemDraft(item);
  const [review, setReview] = useState<{ on: boolean; start: (() => void) | null }>({ on: false, start: null });
  const setReviewing = useCallback((on: boolean, start?: () => void) => setReview({ on, start: on ? start ?? null : null }), []);
  const { view, error } = draft;
  useEffect(() => { if (error) showToast(error, 6000); }, [error]);
  const value = useMemo<DraftCtx>(() => {
    const ops = view?.ops ?? [];
    const shown = ops.length && view ? { ...item, chain_definition: { ...item.chain_definition, nodes: view.nodes } } : item;
    const nodes = shown.chain_definition.nodes ?? [];
    return {
      draft, raw: item, shown, ops, changes: count(ops), issues: view ? issues(view) : [],
      seams: draft.status === "ready" ? seamsOf(item, nodes) : [],
      editable: (node) => draft.status === "ready" && editableAt(item, nodes, node),
      reviewing: review.on, starting: review.start, setReviewing, reload,
    };
  }, [draft, view, item, review, setReviewing, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
