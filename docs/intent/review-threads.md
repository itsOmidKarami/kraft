# Intent: review threads

A person reviewing a work item's changes: drafting and publishing comments,
threads that track resolution and carry suggestions and replies, submitting a
review with an outcome (`approve`, `request_changes`, `comment`) that a gate
call turns into a decision, an agent replying to a thread under its own
session's identity, and every gate attempt pinned to the commit it ran
against so a rebase can't lose it.

`enforced-by:` names the test that pins each requirement.

## REQ review-submission-publishes-drafts
WHEN a review is submitted, the system SHALL publish every draft comment it
covers, and SHALL NOT let a published comment be edited or deleted.
enforced-by: tests/api/test_review.py::test_thread_lifecycle_draft_edit_then_locked_after_submit, tests/store/test_review.py::test_a_new_thread_is_a_draft_until_submitted
origin: src/kraft/store/review.py

## REQ refused-review-records-nothing
IF the gate call a review makes is refused, THEN the system SHALL record no
review and SHALL leave its drafts as drafts.
enforced-by: tests/api/test_review.py::test_approve_that_the_gate_refuses_writes_no_review, tests/api/test_review.py::test_request_changes_to_a_bad_node_writes_no_review, tests/api/test_review.py::test_an_unreadable_head_refuses_the_review_and_records_nothing
origin: src/kraft/api/routes/review.py

## REQ drafts-published-before-the-walk-starts
WHEN a review starts a walk, the system SHALL have published its drafts before
the walk starts.
enforced-by: tests/api/test_review.py::test_drafts_are_submitted_before_the_gate_call_runs
origin: src/kraft/api/routes/review.py

## REQ unresolved-must-fix-blocks-every-approval
WHILE a `must_fix` thread is not resolved, the system SHALL refuse every
approval of a gate, by a person or by an agent.
enforced-by: tests/api/test_review.py::test_approve_is_refused_while_a_must_fix_is_open_even_a_draft, tests/executor/test_gates_autoreview.py::test_a_published_must_fix_thread_downgrades_an_approve_to_undecided, tests/store/test_review.py::test_a_must_fix_filed_at_any_gate_or_none_blocks
origin: src/kraft/api/routes/gates.py

## REQ threads-can-be-filed-without-a-pending-gate
WHEN a person files a review thread on a work item with no pending gate, the
system SHALL accept it and record that it was filed with no gate.
enforced-by: tests/api/test_review.py::test_threads_can_be_filed_without_a_pending_gate
origin: src/kraft/api/routes/review.py

## REQ threads-refused-on-a-finished-item
IF a person files a review thread on a completed or abandoned work item, THEN
the system SHALL refuse it.
enforced-by: tests/api/test_review.py::test_threads_are_refused_on_a_finished_item
origin: src/kraft/api/routes/review.py

## REQ must-fix-blocks-approval-wherever-it-was-filed
WHILE a `must_fix` thread on a work item is not resolved, the system SHALL
refuse approving any of that item's gates, wherever the thread was filed.
enforced-by: tests/store/test_review.py::test_a_must_fix_filed_at_any_gate_or_none_blocks
origin: src/kraft/store/review.py

## REQ request-changes-sends-open-threads-as-the-note
WHEN a person requests changes, the system SHALL send every unresolved
thread, with its suggestions and replies, as the note the re-run agent
receives.
enforced-by: tests/api/test_review.py::test_request_changes_sends_the_threads_as_the_note, tests/store/test_review.py::test_render_note_lists_unresolved_threads_with_suggestions_and_replies
origin: src/kraft/store/review.py

## REQ agent-reply-author-comes-from-its-session
WHEN an agent replies to a thread, the system SHALL take the reply's author
from the agent's session, not from the request.
enforced-by: tests/api/test_review.py::test_agent_reply_takes_its_author_from_the_session
origin: src/kraft/api/routes/review.py

## REQ worker-agents-refused-on-human-review-routes
IF a worker session calls a human review route, THEN the system SHALL refuse
it.
enforced-by: tests/api/test_review.py::test_worker_agents_cannot_use_human_routes
origin: src/kraft/api/routes/review.py

## REQ agent-reply-limited-to-its-own-item
IF an agent replies to a thread of another work item, or to a draft, THEN the
system SHALL refuse the reply.
enforced-by: tests/api/test_review.py::test_reply_door_refuses_humans_and_other_items, tests/api/test_review.py::test_an_agent_cannot_reply_to_a_draft
origin: src/kraft/api/routes/review.py

## REQ agent-claim-never-reopens-a-thread
WHEN an agent's reply carries a claim, the system SHALL move only an open
thread to claimed, and SHALL NOT change a resolved thread's state.
enforced-by: tests/store/test_review.py::test_agent_claim_moves_state_and_reply_without_claim_does_not, tests/store/test_review.py::test_agent_claim_does_not_reopen_a_resolved_thread
origin: src/kraft/store/review.py

## REQ comment-review-at-a-gate-launches-a-reply-agent
WHEN a `comment` review is submitted at a pending gate, the system SHALL
launch a reply agent under that gate and leave the gate pending.
enforced-by: tests/api/test_review.py::test_a_comment_review_gets_answers_and_leaves_the_gate_pending, tests/test_review_reply.py::test_the_reply_agent_runs_under_the_gate_with_edit_denied
origin: src/kraft/review_reply.py

## REQ reply-agent-failure-is-recorded-not-escalated
IF the reply agent fails, THEN the system SHALL record the failure and SHALL
NOT stop or escalate the work item.
enforced-by: tests/test_review_reply.py::test_a_crashing_reply_agent_leaves_the_thread_unanswered_not_escalated, tests/test_review_reply.py::test_a_failure_before_the_launch_is_recorded_not_escalated
origin: src/kraft/review_reply.py

## REQ reply-agent-worktree-write-is-recorded
IF the reply agent changes the worktree, THEN the system SHALL record the
files it changed and SHALL NOT stop the work item.
enforced-by: tests/test_review_reply.py::test_a_write_to_the_worktree_is_recorded_not_escalated
origin: src/kraft/review_reply.py

## REQ every-gate-attempt-is-pinned-and-survives-a-rebase
WHEN a node completes or a gate is requested, the system SHALL pin the commit
it ended on under a ref that keeps it reachable after a rebase.
enforced-by: tests/test_node_runs.py::test_completed_pins_a_ref_that_survives_a_reset, tests/api/test_review.py::test_reaching_a_gate_pins_the_node_and_the_gate
origin: src/kraft/node_runs.py

## REQ re-requesting-a-gate-at-the-same-commit-adds-no-attempt
WHEN a gate is requested again at the commit its last attempt pinned, the
system SHALL NOT record a new attempt.
enforced-by: tests/store/test_review.py::test_pin_gate_dedupes_a_rerequest_at_the_same_head
origin: src/kraft/store/review.py

## REQ abandon-and-archive-drop-attempt-refs
WHEN a work item is abandoned or archived, the system SHALL delete its
attempt refs.
enforced-by: tests/api/test_review.py::test_abandon_drops_the_items_refs, tests/api/test_review.py::test_archive_drops_the_items_refs
origin: src/kraft/api/routes/lifecycle.py

## REQ compare-against-a-missing-commit-is-an-error
IF a compare target's commit no longer exists, THEN the system SHALL answer
with an error, not an empty diff.
enforced-by: tests/api/test_review.py::test_compare_with_a_vanished_sha_is_an_error_not_empty
origin: src/kraft/api/routes/artifacts.py
