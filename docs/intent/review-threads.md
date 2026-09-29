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

## REQ approve-needs-a-pending-gate
IF a person approves with no gate pending, THEN the system SHALL refuse it.
enforced-by: tests/api/test_review.py::test_approve_needs_a_pending_gate
origin: src/kraft/api/routes/review.py

## REQ gateless-comment-nothing-downstream-reads-is-refused
IF a person sends review threads with no gate pending and nothing ahead in the
chain can read them, THEN the system SHALL refuse the review and record nothing.
enforced-by: tests/api/test_lifecycle.py::test_review_reachable_counts_agents_and_gates_ahead_but_not_behind, tests/api/test_review.py::test_a_gateless_comment_is_recorded_with_no_gate, tests/api/test_review.py::test_a_gateless_comment_is_refused_when_nothing_ahead_can_read_it
origin: src/kraft/api/routes/lifecycle.py

## REQ working-agent-dispatch-carries-unanswered-threads
WHEN an agent task with no skill is dispatched, the system SHALL include every
unanswered review thread on its item, with the instruction to reply to each;
in a sandbox with no route to Kraft (no `network:`), to say in its result what
it did about each instead.
enforced-by: tests/executor/test_prompts.py::test_a_working_agent_is_told_to_address_and_reply[host], tests/executor/test_prompts.py::test_a_working_agent_is_told_to_address_and_reply[no-network], tests/executor/test_dispatch_review_threads.py::test_the_implementer_prompt_carries_a_mid_run_thread, tests/executor/test_dispatch_review_threads.py::test_a_sandboxed_worker_is_told_to_run_kraft_only_with_a_route_to_it[no-network]
origin: src/kraft/executor/prompts.py

## REQ reviewer-dispatch-judges-against-unanswered-threads
WHEN an agent task with a skill is dispatched, the system SHALL include every
unanswered review thread on its item as something to judge the change
against, and SHALL NOT tell it to reply.
enforced-by: tests/executor/test_prompts.py::test_a_reviewer_judges_against_the_threads_and_does_not_reply
origin: src/kraft/executor/prompts.py

## REQ a-thread-in-the-rejection-note-is-not-listed-twice
WHEN a dispatch's note already lists a review thread, the system SHALL NOT
list that thread again with the unanswered threads.
enforced-by: tests/executor/test_prompts.py::test_answered_resolved_and_already_in_the_note_threads_are_left_out
origin: src/kraft/executor/prompts.py

## REQ request-changes-target-comes-from-the-threads
WHEN a person requests changes with no gate pending and names no node, the
system SHALL re-run the earliest node, at or before the current one, that
wrote what the threads are about, and SHALL say which node and why.
enforced-by: tests/test_review.py::test_the_target_is_the_node_that_wrote_the_threads_file, tests/test_review.py::test_whole_change_threads_fall_back_to_the_current_working_node, tests/test_review.py::test_a_file_only_a_subprocess_node_touched_targets_the_earlier_working_node
origin: src/kraft/review.py

## REQ request-changes-reruns-the-running-target-node
WHEN a person requests changes and the target is the node that is running,
the system SHALL stop that node and re-run it with the review as its note.
enforced-by: tests/api/test_review.py::test_request_changes_on_the_running_node_reruns_it
origin: src/kraft/api/routes/review.py

## REQ request-changes-for-an-earlier-node-rewinds-when-the-running-node-completes
WHEN a person requests changes at an earlier node while another runs, the
system SHALL NOT stop the running node, and SHALL re-run the target when the
running node completes.
enforced-by: tests/api/test_review.py::test_request_changes_for_an_earlier_node_does_not_stop_the_running_one, tests/executor/test_walk_rewind.py::test_a_pending_rewind_takes_effect_when_the_node_completes, tests/executor/test_walk_rewind.py::test_a_ci_wait_resume_does_not_jump_to_a_pending_rewind
origin: src/kraft/executor/walk.py

## REQ a-pending-rewind-is-honoured-by-the-next-resume-or-retry
WHILE a requested rewind is pending, the system SHALL start the next person's
resume, or retry with no path, at its target with its note.
enforced-by: tests/api/test_review.py::test_a_pending_rewind_is_honoured_by_the_next_resume, tests/api/test_review.py::test_request_changes_on_a_stopped_item_retries_at_the_target
origin: src/kraft/api/routes/lifecycle.py

## REQ request-changes-never-resumes-a-paused-item
WHEN a person requests changes on a paused work item, the system SHALL record
the rewind and SHALL NOT resume the item.
enforced-by: tests/api/test_review.py::test_request_changes_never_resumes_a_paused_item
origin: src/kraft/api/routes/review.py

## REQ gate-bounces-on-an-unanswered-must-fix
WHEN the walk reaches a gate while a `must_fix` thread is unanswered, the
system SHALL reject the gate on the person's behalf with the threads as the
note, instead of opening it.
enforced-by: tests/executor/test_gates_feedback.py::test_a_gate_with_an_unanswered_must_fix_bounces_instead_of_opening, tests/executor/test_gates_feedback.py::test_an_earlier_gates_must_fix_still_blocks_a_later_gate
origin: src/kraft/executor/gates.py

## REQ gate-opens-with-replies-for-unanswered-questions
WHEN the walk opens a gate while only non-must-fix threads are unanswered, the
system SHALL launch the reply agent for them as the gate opens.
enforced-by: tests/executor/test_gates_feedback.py::test_a_gate_with_only_unanswered_questions_opens_with_the_reply_agent
origin: src/kraft/executor/walk.py

## REQ human-feedback-bounce-counts-against-the-reject-cap
IF bouncing a gate on unanswered feedback would breach the gate's reject-loop
cap, THEN the system SHALL open the gate for the person instead.
enforced-by: tests/executor/test_gates_feedback.py::test_the_bounce_counts_against_the_reject_cap_and_then_opens
origin: src/kraft/executor/gates.py

## REQ review-cli-verbs-reach-their-routes
WHEN a person runs a review verb of `kraft` or calls its MCP tool, the system
SHALL make the same request the API route takes.
enforced-by: tests/cli/test_review_threads.py::test_a_review_verb_passes_its_arguments_through[comment-new-thread], tests/cli/test_review_threads.py::test_a_review_verb_passes_its_arguments_through[comment-reply], tests/cli/test_review_threads.py::test_a_review_verb_passes_its_arguments_through[resolve-thread], tests/cli/test_review_threads.py::test_a_review_verb_passes_its_arguments_through[reopen-thread], tests/cli/test_review_threads.py::test_a_review_verb_passes_its_arguments_through[review-request-changes], tests/cli/test_review_threads.py::test_comment_needs_lines_for_a_suggestion, tests/cli/test_review_threads.py::test_view_threads_renders_a_block_per_thread, tests/cli/test_review_threads.py::test_view_compare_forwards_targets_and_stats_the_files, tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[list_threads-threads-args0], tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[compare_changes-compare-args1], tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[add_review_comment-add_review_comment-args2], tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[resolve_thread-resolve_thread-args3], tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[reopen_thread-reopen_thread-args4], tests/test_mcp.py::test_each_review_tool_delegates_to_its_client_function[submit_review-submit_review-args5], tests/test_mcp.py::test_submit_review_says_only_a_human_should_decide, tests/client/test_act.py::test_review_verbs_send_the_payloads_the_thread_api_accepts
origin: src/kraft/client/actions.py
