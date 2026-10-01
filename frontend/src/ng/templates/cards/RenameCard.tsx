import type { RefObject } from "react";
import { IdCard } from "../menus/IdCard";
import type { Ref } from "./refs";

/** Rename (Decisions §9 Rename): the id with its text selected, and the
 *  references Enter updates in the same draft change. */
export function RenameCard({ anchor, what, id, taken, refs, refused, chain, onGo, onClose }: {
  anchor: RefObject<HTMLElement | null>;
  what: string;
  id: string;
  taken: string[];
  refs: Ref[];
  refused?: string | null;
  /** Renaming the chain itself: the repos defaulting to it follow it. */
  chain?: boolean;
  onGo: (id: string) => void;
  onClose: () => void;
}) {
  const note = refs.length ? (
    <>
      <span>Enter also updates {refs.length} reference{refs.length === 1 ? "" : "s"}:</span>
      <ul className="card-refs">{refs.map((r) => <li key={r.path}>{r.path}</li>)}</ul>
    </>
  ) : chain ? "Repos defaulting to it follow the new id when you publish." : "Nothing else refers to it.";
  return <IdCard anchor={anchor} title={`Rename ${what}`} initial={id} taken={taken.filter((x) => x !== id)} go="Rename" refused={refused} note={note} onGo={(nid) => (nid === id ? onClose() : onGo(nid))} onClose={onClose} />;
}
