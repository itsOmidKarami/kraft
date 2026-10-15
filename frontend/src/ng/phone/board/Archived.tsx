import { useEffect, useState } from "react";
import * as api from "../../../api";
import { ago, repoName } from "../../../format";
import type { WorkItem } from "../../../types";
import { AreaScreen } from "../areas/AreaScreen";
import { Group, type RowSpec } from "../areas/kit";
import { archivedLede, ARCHIVED_READ_ERROR } from "../../board/archivedWords";
import { itemPath } from "./actions";

/** `/archived`: the desktop's archived list as rows, newest first, so its
 *  address answers at phone width too. A row opens the item, which restores it. */
export function Archived() {
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState<number | null>(null);
  useEffect(() => {
    api.getPolicy().then((p) => setDays(p.archive?.after_days ?? null)).catch(() => {});
    api.listArchivedWorkItems().then((r) => setItems(r.items ?? []), (e: Error) => setError(e.message));
  }, []);
  const shown = [...(items ?? [])].sort((a, b) => (b.archived_at ?? "").localeCompare(a.archived_at ?? ""));
  return (
    <AreaScreen title="Archived" sub={archivedLede(days)} yaml={false}>
      {error && <p className="ph-error" role="alert">{ARCHIVED_READ_ERROR(error)}</p>}
      {items && items.length === 0 && <p className="ph-empty">Nothing is archived.</p>}
      {shown.length > 0 && (
        <Group rows={shown.map((i): RowSpec => ({ key: i.id, label: i.title, sub: [repoName(i.repo), i.archived_at ? `archived ${ago(i.archived_at)}` : null].filter(Boolean).join(" · "), to: itemPath(i.id) }))} />
      )}
    </AreaScreen>
  );
}
