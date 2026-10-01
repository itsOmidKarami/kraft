import { useEffect, useState } from "react";
import * as api from "../../api";
import type { WorkItemDocument } from "../../types";

/** The item's documents, read once per item version (Documents under a task's Result). */
export function useDocuments(id: string, version: string): WorkItemDocument[] {
  const [docs, setDocs] = useState<WorkItemDocument[]>([]);
  useEffect(() => {
    let live = true;
    api.getWorkItemDocuments(id).then((r) => live && setDocs(r.documents), () => live && setDocs([]));
    return () => { live = false; };
  }, [id, version]);
  return docs;
}
