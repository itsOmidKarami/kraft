# Intent: gates

Intended behaviour of Kraft's human gates: when one opens, what approving and
rejecting do, and what a reviewer is shown at one. Backfilled from Kraft's
design record without reading the tests or `src/kraft/`, so that what the
system is *supposed* to do is written down independently of what it does.

`enforced-by:` names the test that pins each requirement. An `origin:` line,
where one survives, names a file in this repository.

## REQ node-with-gate-after-opens-gate
WHEN every task in a node has reached a terminal state and that node declares a
`gate_after` name, the system SHALL open a gate of that name.
enforced-by: tests/test_gates.py::test_walk_stops_at_first_gate, tests/test_gates.py::test_approving_all_four_gates_completes_chain

## REQ node-without-gate-after-opens-no-gate
IF a node declares no `gate_after` name, THEN the system SHALL NOT open a gate
after that node.
enforced-by: tests/test_gates.py::test_approving_all_four_gates_completes_chain
origin: templates/default.yaml

## REQ open-gate-sets-needs-human
WHEN a gate opens, the system SHALL set the work item's status to `needs_human`.
enforced-by: tests/test_gates.py::test_walk_stops_at_first_gate

## REQ open-gate-emits-gate-requested
WHEN a gate opens, the system SHALL emit a `gate_requested` event carrying the
gate's name in its payload.
enforced-by: tests/test_gates.py::test_walk_stops_at_first_gate

## REQ open-gate-halts-the-chain
WHILE a gate is open, the system SHALL NOT start the next node of the chain.
enforced-by: tests/test_gates.py::test_walk_stops_at_first_gate

## REQ approve-emits-gate-approved
WHEN a gate is approved, the system SHALL emit a `gate_approved` event carrying
the gate's name in its payload.
enforced-by: tests/test_gates.py::test_approving_all_four_gates_completes_chain

## REQ approve-advances-to-next-node
WHEN a gate is approved, the system SHALL advance the work item to the next node
in its chain.
enforced-by: tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve

## REQ reject-without-a-note-is-rejected
IF a gate rejection carries no note, THEN the system SHALL reject the request.
enforced-by: tests/api/test_gates.py::test_gate_reject_requires_note_and_re_runs_the_producer

## REQ reject-without-a-note-leaves-the-gate-open
IF a gate rejection carries no note, THEN the system SHALL leave the gate open.

## REQ reject-emits-gate-rejected-with-the-note
WHEN a gate is rejected, the system SHALL emit a `gate_rejected` event whose
payload carries the reviewer's note.
enforced-by: tests/test_gates.py::test_reject_records_the_note_and_reopen_flips_the_row

## REQ producer-gate-reject-reinvokes-the-producing-hook
WHEN a `spec_approval` or `plan_approval` gate is rejected, the system SHALL
re-invoke that node's own producing hook.
enforced-by: tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve, tests/test_planning_chain.py::test_a_rejected_plan_rerun_is_framed_as_a_revision, tests/api/test_gates.py::test_gate_reject_requires_note_and_re_runs_the_producer

## REQ chain-finalized-reject-reinvokes-chain-review
WHEN a `chain_finalized` gate is rejected, the system SHALL re-invoke
`on.chain.review_ready`.

## REQ reject-note-is-injected-into-the-reinvocation
WHEN the system re-invokes a producing hook after a gate rejection, the system
SHALL include the reviewer's note in the prompt of that invocation.
enforced-by: tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve, tests/test_planning_chain.py::test_a_rejected_plan_rerun_is_framed_as_a_revision

## REQ producer-gate-reject-increments-its-reject-loop-counter
WHEN a `spec_approval` gate is rejected, the system SHALL increment the
`spec_approval_reject_loop` counter for that work item.
enforced-by: tests/api/test_gates.py::test_gate_reject_is_bounded_by_its_reject_loop

## REQ plan-approval-reject-increments-its-reject-loop-counter
WHEN a `plan_approval` gate is rejected, the system SHALL increment the
`plan_approval_reject_loop` counter for that work item.

## REQ chain-finalized-reject-increments-its-reject-loop-counter
WHEN a `chain_finalized` gate is rejected, the system SHALL increment the
`chain_finalized_reject_loop` counter for that work item.

## REQ reject-loop-breach-stops-reinvocation
IF a `<gate>_reject_loop` counter is at its cap, THEN the system SHALL NOT
re-invoke the node's producing hook.

## REQ reject-loop-breach-sets-needs-human
IF a `<gate>_reject_loop` counter is at its cap, THEN the system SHALL leave the
work item in `needs_human`.
enforced-by: tests/skills/test_gate_review.py::test_repeated_fixed_verdicts_breach_the_reject_loop, tests/api/test_gates.py::test_gate_reject_is_bounded_by_its_reject_loop

## REQ reinvoked-node-reopens-its-own-gate
WHEN a producing hook re-invoked after a rejection completes, the system SHALL
open that node's gate again.
enforced-by: tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve

## REQ reject-to-re-enters-the-named-node
WHERE a gate's node declares a `reject_to` node, the system SHALL move the work
item to that node when the gate is rejected.
enforced-by: tests/skills/test_gate_review.py::test_verdict_reenters_the_walk_at_the_right_node[reject-0], tests/api/test_gates.py::test_rejecting_the_final_gate_re_enters_at_implementation
origin: templates/default.yaml

## REQ reject-to-carries-the-note-as-steer
WHERE a gate's node declares a `reject_to` node, the system SHALL carry the
reviewer's note into the re-entered node as steer context.
enforced-by: tests/skills/test_gate_review.py::test_verdict_reenters_the_walk_at_the_right_node[reject-0], tests/api/test_gates.py::test_rejecting_the_final_gate_re_enters_at_implementation
origin: templates/default.yaml

## REQ chain-review-gate-requires-a-plan-node
IF the loaded chain contains no `plan` node, THEN the system SHALL NOT open the
`chain_finalized` gate.

## REQ chain-finalized-approval-splices-the-revised-chain
WHEN the `chain_finalized` gate is approved, the system SHALL splice the revised
chain nodes into the work item's `chain_definition`.
enforced-by: tests/api/test_gates.py::test_chain_review_splice_runs_the_revised_tail

## REQ chain-revision-leaves-executed-nodes-alone
WHEN the system splices a revised chain into `chain_definition`, the system SHALL
NOT modify nodes that have already executed.

## REQ gate-artifact-comes-from-the-gate-node-tasks
WHEN a gate opens, the system SHALL expose as the gate's artifact a document
produced by that same node's own tasks.
enforced-by: tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve
origin: templates/default.yaml

## REQ spec-worker-without-artifact-opens-no-gate
IF a spec node's worker wrote no artifact, THEN the system SHALL fail the node
rather than open a gate.
enforced-by: tests/test_gates.py::test_a_spec_worker_that_wrote_no_artifact_opens_no_gate

## REQ gate-without-its-artifact-stays-answerable
IF a pending gate's artifact is not on disk, THEN the system SHALL still accept a
decision on that gate.
enforced-by: tests/api/test_artifact.py::test_approving_a_gate_with_no_artifact_ingests_nothing

## REQ intake-copies-a-gate-trimming-attachment
WHEN intake trims a gate for an attachment, the system SHALL copy that attachment
into Kraft's own storage.
enforced-by: tests/executor/test_entry.py::test_intake_copies_an_attachment_into_kraft_storage, tests/executor/test_entry.py::test_the_copy_survives_the_original_being_deleted

## REQ intake-refuses-an-uncopyable-attachment
IF intake cannot copy an attachment, THEN the system SHALL refuse the intake.
enforced-by: tests/executor/test_entry.py::test_intake_refuses_an_attachment_it_cannot_copy

## REQ missing-stored-attachment-fails-loudly
IF a trimmed gate's stored attachment is missing when the worktree is prepared,
THEN the system SHALL fail rather than run without it.
enforced-by: tests/test_builtins.py::test_a_missing_attachment_source_fails_loudly

## REQ human-review-gate-offers-a-diff
WHEN a `human_review_approval` gate opens, the system SHALL offer the work item's
diff as the gate's review surface.

## REQ document-gates-offer-no-diff
IF an open gate is not `human_review_approval`, THEN the system SHALL NOT offer a
diff at that gate.

## REQ env-setup-pins-the-diff-base
WHEN the `env_setup` node creates a work item's worktree, the system SHALL record
the source repo's current HEAD as that work item's `base_ref`.
enforced-by: tests/test_builtins.py::test_env_setup_stamps_base_ref

## REQ env-setup-re-entry-does-not-repin-the-base
IF the `env_setup` node re-enters against an existing worktree, THEN the system
SHALL NOT overwrite the work item's `base_ref`.
enforced-by: tests/test_builtins.py::test_env_setup_does_not_restamp_on_reentry, tests/test_builtins.py::test_ensure_worktree_is_idempotent_and_pins_base_ref_once

## REQ diff-includes-uncommitted-work
The system SHALL include uncommitted worktree changes in the diff it serves for a
work item.
enforced-by: tests/api/test_diff.py::test_diff_splits_landed_commits_from_in_flight_work, tests/api/test_diff.py::test_diff_landed_is_empty_when_nothing_is_committed

## REQ diff-lists-untracked-files-separately
WHEN a work item's worktree contains untracked files, the system SHALL report
their paths in a list separate from the diff body.
enforced-by: tests/api/test_diff.py::test_diff_lists_untracked_without_adding_them, tests/api/test_diff.py::test_diff_lists_untracked_files_inside_a_new_directory

## REQ null-base-ref-yields-an-empty-diff
IF a work item's `base_ref` is null, THEN the system SHALL return a success
response with an empty diff.
enforced-by: tests/api/test_diff.py::test_diff_with_null_base_ref_is_empty_not_an_error

## REQ missing-worktree-is-not-found
IF a work item's `base_ref` is set and its worktree directory does not exist, THEN
the system SHALL respond 404 to a diff request.
enforced-by: tests/api/test_diff.py::test_diff_404s_when_the_worktree_is_gone

## REQ failed-git-diff-is-an-error-not-an-empty-diff
IF `git diff` exits non-zero, THEN the system SHALL respond 500.
enforced-by: tests/api/test_diff.py::test_diff_500s_when_git_fails_rather_than_returning_empty

## REQ oversized-diff-is-flagged-truncated
IF the diff body exceeds the size cap, THEN the system SHALL set `truncated` on
the response.
enforced-by: tests/api/test_diff.py::test_diff_truncates_at_a_file_boundary

## REQ truncated-diff-keeps-the-full-file-list
IF the diff body exceeds the size cap, THEN the system SHALL still return the
complete file list.
enforced-by: tests/api/test_diff.py::test_diff_truncates_at_a_file_boundary

## REQ truncated-diff-cuts-at-a-file-boundary
IF the diff body exceeds the size cap, THEN the system SHALL cut the returned body
at a file boundary.
enforced-by: tests/api/test_diff.py::test_diff_truncates_at_a_file_boundary, tests/api/test_diff.py::test_truncate_bounds_a_single_file_bigger_than_the_cap

## REQ pause-is-available-between-gates
WHILE a work item is running an unattended stretch, the system SHALL accept a
pause request for it.
enforced-by: tests/test_pause_resume.py::test_pause_then_resume_with_a_steer_relaunches_the_task

## REQ resume-does-not-consume-a-retry-attempt
WHEN a paused work item is resumed, the system SHALL NOT increment
`retry_counters.attempts`.
