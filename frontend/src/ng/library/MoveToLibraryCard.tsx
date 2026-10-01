import { useMemo, useState, type RefObject } from "react";
import { detailOf } from "../http";
import { showToast } from "../ui/Toast";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import type { Authored } from "../templates/draft/types";
import { LIBRARY_FILE } from "../templates/draft/view";
import { IdCard } from "../templates/menus/IdCard";
import { useLibrary } from "../templates/useLibrary";
import { moveLines, type Movable } from "./moveToLibrary";

/** Move to library (Decisions §9, R47): the exec node or task becomes a library component and the chain keeps
 *  `{ id, extends }`. The card names the id, says what the library gains and what the chain keeps, and refuses a
 *  taken id inline. The library file joins this chain's draft, and publishing writes both. */
export function MoveToLibraryCard({ anchor, draft, path, id, own, what, onMoved, onClose }: {
  anchor: RefObject<HTMLElement | null>;
  draft: ConfigDraft;
  path: string;
  id: string;
  own: Authored;
  what: Movable;
  onMoved: () => void;
  onClose: () => void;
}) {
  const library = useLibrary();
  const [refused, setRefused] = useState<string | null>(null);
  const [name, setName] = useState(id);
  // The names the section has: published, and whatever a draft already holds in `library.yaml`.
  const taken = useMemo(() => {
    const published = typeof library === "string" ? [] : library.filter((c) => c.kind === what.section).map((c) => c.name);
    const drafted = Object.keys(((draft.view!.result.model[LIBRARY_FILE] ?? {}) as Record<string, Record<string, unknown> | undefined>)[what.section] ?? {});
    return [...new Set([...published, ...drafted])];
  }, [library, draft.view, what.section]);
  const lines = moveLines(draft.scope.key, what.section, name, id, own);
  return (
    <IdCard
      anchor={anchor}
      title="Move to library"
      initial={id}
      taken={taken}
      go="Move"
      refused={refused}
      note={<><span className="lib-line">{lines.gains}</span><span className="lib-line">{lines.keeps}</span><span className="lib-line">⌘Z undoes it. Publishing writes both files.</span></>}
      onChange={setName}
      onGo={async (to) => {
        const a = await draft.ops([{ op: "move_to_library", path, name: to }], { quiet: true });
        if (a.status !== 200) return setRefused(detailOf(a.body));
        onClose();
        showToast(`Moved to the library as ${what.section}.${to} · ⌘Z undoes it`);
        onMoved();
      }}
      onClose={onClose}
    />
  );
}
