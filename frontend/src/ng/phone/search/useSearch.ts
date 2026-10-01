import { useEffect, useRef, useState } from "react";
import * as api from "../../../api";
import type { Bead, SearchResult } from "../../../types";

export type Load = "idle" | "loading" | "ok" | "error";
export interface Filters {
  source: string;
  kind: string;
  repo: string;
  mode: string;
}

export const DEBOUNCE_MS = 250;

/** Documents and beads for a query (W17 brief I). Debounced, and an answer for an older query is dropped, so a slow search can never overwrite a newer one. */
export function useSearch(query: string, f: Filters) {
  const [docs, setDocs] = useState<{ load: Load; results: SearchResult[]; error?: string }>({ load: "idle", results: [] });
  const [beads, setBeads] = useState<{ load: Load; list: Bead[] }>({ load: "idle", list: [] });
  const seq = useRef(0);
  useEffect(() => {
    if (!query) {
      seq.current++;
      setDocs({ load: "idle", results: [] });
      setBeads({ load: "idle", list: [] });
      return;
    }
    const mine = ++seq.current;
    // The last answer stays while the next one loads.
    setDocs((d) => ({ ...d, load: "loading" }));
    setBeads((b) => ({ ...b, load: "loading" }));
    const t = setTimeout(() => {
      api
        .search({ q: query, mode: f.mode || undefined, source_kind: f.source || undefined, kind: f.kind || undefined, repo: f.repo || undefined, limit: 8 })
        .then((r) => mine === seq.current && setDocs({ load: "ok", results: r.results.slice(0, 8) }))
        .catch((e: Error) => mine === seq.current && setDocs({ load: "error", results: [], error: e.message }));
      api
        .searchBeads(query)
        .then((r) => mine === seq.current && setBeads({ load: "ok", list: r.beads }))
        .catch(() => mine === seq.current && setBeads({ load: "error", list: [] }));
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query, f.source, f.kind, f.repo, f.mode]);
  return { docs, beads };
}
