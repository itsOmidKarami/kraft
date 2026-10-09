/** The review flow's wire shapes: `GET /work-items/{id}/compare`, the thread
 *  routes (`api/routes/review.py`, `store.threads_for`) and review submission.
 *  Only the new UI reads these today (Kraft-3spjr). */

import type {
  DiffSide,
  ReplyClaim,
  ReviewOutcome,
  ThreadLabel,
  ThreadState,
} from "./vocab.generated";

export type { ReviewOutcome, ThreadLabel, ThreadState };

/** What `/compare` resolves: the base commit, a gate attempt, the last
 *  submitted review, or the working tree. */
export type CompareTarget = "base" | "latest" | "last_review" | `attempt:${number}`;

export interface CompareFile {
  /** The new path; a rename is reported under it. */
  path: string;
  insertions: number;
  deletions: number;
  /** The nodes whose runs changed this file inside the comparison, in run order. */
  touched_by: string[];
  /** Marked viewed against this comparison's `to`, and unchanged since (B11). */
  viewed: boolean;
}

export interface Compare {
  from: { target: CompareTarget; sha: string | null };
  /** `sha` null: the working tree. */
  to: { target: CompareTarget; sha: string | null };
  /** The two targets sit on different bases: a rebase happened between them. */
  rebased: boolean;
  files: CompareFile[];
  /** Only when the request named `nodes`. */
  groups: { node_id: string; files: string[] }[];
  /** Unified diff text, cut at a file boundary when over `diff_max_bytes`. */
  diff: string;
  untracked: string[];
  truncated: boolean;
  ignore_whitespace: boolean;
  diff_max_bytes: number;
}

export interface Suggestion {
  start_line: number;
  end_line: number;
  replacement: string;
}

export interface ReviewComment {
  id: string;
  thread_id: string;
  /** Set once the review carrying it is submitted; null while it is a draft. */
  review_id: string | null;
  /** `"you"`, or the id of the node whose agent replied. */
  author: string;
  attempt: number | null;
  body: string;
  suggestion: Suggestion | null;
  claim: ReplyClaim | null;
  created_at: string;
  draft: boolean;
}

export interface ReviewThread {
  id: string;
  work_item_id: string;
  gate: string | null;
  node_id: string | null;
  file_path: string | null;
  /** The side of `end_line`. */
  side: DiffSide | null;
  /** The side of `start_line`: `side`, unless the range runs across sides. */
  start_side?: DiffSide | null;
  start_line: number | null;
  end_line: number | null;
  /** The range's lines when it was commented on, each led by its diff mark. */
  quote?: string | null;
  anchor_sha: string;
  label: ThreadLabel | null;
  state: ThreadState;
  resolved_at: string | null;
  created_at: string;
  /** The first is the thread's own body. */
  comments: ReviewComment[];
  /** Not yet part of a submitted review. */
  draft: boolean;
}

/** A gateless `request_changes`: where it sent the work and what it did. */
export interface GatelessChanges {
  review_id: string;
  outcome: "request_changes";
  gate: null;
  target: string;
  target_reason: string;
  action: "retried" | "rerun" | "queued";
}

/** A `comment` review. */
export interface CommentReview {
  review_id: string;
  outcome: "comment";
  gate: string | null;
  reply_agent: boolean;
}
