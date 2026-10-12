import type { ReviewComment, ReviewThread } from "../../../src/core/api";

export const C = (over: Partial<ReviewComment> = {}): ReviewComment => ({
  id: "c1", thread_id: "t1", review_id: "r1", author: "you", attempt: null, body: "b", suggestion: null, claim: null,
  created_at: "2026-10-12T00:00:00Z", draft: false, ...over,
});

export const T = (over: Partial<ReviewThread> = {}): ReviewThread => ({
  id: "t1", work_item_id: "K-1", gate: null, node_id: null, file_path: "src/a.py", side: "new", start_side: null,
  start_line: 1, end_line: 1, quote: null, anchor_sha: "abc", label: null, state: "open", resolved_at: null,
  created_at: "2026-10-12T00:00:00Z", comments: [C()], draft: false, ...over,
});
