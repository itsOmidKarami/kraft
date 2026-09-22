export interface DocumentLink {
  work_item_id: string | null;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}

export interface WorkItemDocument {
  document_id: string;
  repo: string;
  title: string;
  kind: string | null;
  source_kind: string;
  path: string;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
  /** Set when this row is an intake attachment rather than an agent-written link. */
  attachment_kind: "spec" | "plan" | null;
  /** Server-sorted newest first; the only time field every document kind has. */
  indexed_at: string;
  /** W13 A: the run the linked worker session was — null for artifacts and
   *  attachments. Optional: an older server does not send them. */
  attempt?: number | null;
  round?: number | null;
  session_status?: string | null;
}

export interface DiffFile {
  path: string;
  insertions: number;
  deletions: number;
}

export interface WorkItemDiff {
  work_item_id: string;
  base_ref: string | null;
  /** In-flight: `HEAD`..working tree, the change under review. */
  files: DiffFile[];
  diff: string;
  untracked: string[];
  truncated: boolean;
  /** `base_ref..HEAD` — what earlier nodes committed. Optional: an older
   *  server, and every fixture written before Kraft-nceo, has no such key. */
  landed?: {
    commits: string[];
    files: DiffFile[];
    diff: string;
    truncated: boolean;
  };
}

/** The document a gate is a decision about, read off the worktree. */
export interface WorkItemArtifact {
  work_item_id: string;
  path: string;
  title: string;
  content: string;
  truncated: boolean;
  artifact_max_bytes: number;
  /** A chain revision's; its approval sends it back (Kraft-ec66w). */
  digest?: string;
}

export interface SearchResult {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  snippet: string;
  score: number;
  links: DocumentLink[];
}

export interface SearchResponse {
  query: string;
  mode: string;
  results: SearchResult[];
}

export interface DocumentDetail {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  content: string;
  metadata: Record<string, unknown>;
  source_created_at: string | null;
  source_updated_at: string | null;
  indexed_at: string;
  /**
   * 'git_scan' (`path` is real, under `repo`) or 'event_ingest' (a session
   * summary or gate artifact — `path` is a synthetic identifier, there is no
   * file in the connected repo checkout to open or copy a path to). Absent
   * for a synthetic `attachment:...` document, which predates this column.
   */
  origin?: "git_scan" | "event_ingest";
  links: DocumentLink[];
}

export interface Bead {
  id: string;
  title: string;
  status: string | null;
  issue_type: string | null;
}
