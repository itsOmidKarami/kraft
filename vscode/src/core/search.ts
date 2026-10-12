import { docTitle, repoName } from "../../../frontend/src/format";
import type { SearchResult } from "../../../frontend/src/types/documents";

/** One line of Kraft: Search…'s list: the hit's document, and the work item it
 *  belongs to when one of its links names one. */
export interface SearchPick {
  label: string;
  description: string;
  detail: string;
  itemId: string | null;
  docId: string;
}

/** The server marks the matched words `[like this]`; a quick pick shows them plain. */
const plain = (snippet: string) => snippet.replace(/[[\]]/g, "");

/** Search hits as the quick pick lists them, titled as the web UI titles them (`docTitle`). */
export function searchPicks(results: SearchResult[]): SearchPick[] {
  return results.map((r) => {
    const detail = plain(r.snippet);
    return {
      label: docTitle({ ...r, ...r.links[0], content: detail }),
      description: `${r.kind ?? r.source_kind} · ${repoName(r.repo)}`,
      detail,
      itemId: r.links.find((l) => l.work_item_id)?.work_item_id ?? null,
      docId: r.id,
    };
  });
}

/** Numbers requests so only the latest one's answer lands: `begin()` starts
 *  one and returns whether it is still the latest. A `begin()` with nothing to
 *  send (blank input, a hidden pick) just makes every earlier one stale. */
export function latest(): () => () => boolean {
  let seq = 0;
  return () => {
    const mine = ++seq;
    return () => mine === seq;
  };
}
