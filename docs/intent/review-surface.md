# Intent: the review surface

What a person deciding on a work item is shown and can rely on, independent of
the chain template it runs: a gate that stays answerable, the documents an item
was filed with, and the diff. Carried from the legacy gate spec
(`docs/intent/gates.md`, retired with the legacy template system in Task 11b),
whose chain-shape requirements now live in `templates-v1.md`; these are the ones
that never depended on the chain's shape.

`enforced-by:` names the test that pins each requirement.

## REQ task-without-its-promised-document-opens-no-gate
IF an agent task that declares the document it produces wrote none, THEN the
system SHALL fail the node rather than open the gate after it.
enforced-by: tests/test_gates.py::test_a_spec_worker_that_wrote_no_artifact_opens_no_gate

## REQ gate-without-its-artifact-stays-answerable
IF a pending gate's artifact is not on disk, THEN the system SHALL still accept a
decision on that gate.
enforced-by: tests/api/test_artifact.py::test_approving_a_gate_with_no_artifact_ingests_nothing

## REQ intake-copies-an-attachment
WHEN intake accepts an attachment, the system SHALL copy it into Kraft's own
storage.
enforced-by: tests/executor/test_entry.py::test_intake_copies_an_attachment_into_kraft_storage, tests/executor/test_entry.py::test_the_copy_survives_the_original_being_deleted

## REQ intake-refuses-an-uncopyable-attachment
IF intake cannot copy an attachment, THEN the system SHALL refuse the intake.
enforced-by: tests/executor/test_entry.py::test_intake_refuses_an_attachment_it_cannot_copy

## REQ missing-stored-attachment-fails-loudly
IF an attachment's stored copy is missing when the worktree is prepared, THEN
the system SHALL fail rather than run without it.
enforced-by: tests/test_builtins.py::test_a_missing_attachment_source_fails_loudly

## REQ worktree-preparation-pins-the-diff-base
WHEN the system creates a work item's worktree, it SHALL record the source repo's
current HEAD as that work item's `base_ref`.
enforced-by: tests/test_builtins.py::test_worktree_preparation_stamps_base_ref

## REQ worktree-re-entry-does-not-repin-the-base
IF worktree preparation re-enters against an existing worktree, THEN the system
SHALL NOT overwrite the work item's `base_ref`.
enforced-by: tests/test_builtins.py::test_worktree_preparation_does_not_restamp_on_reentry, tests/test_builtins.py::test_ensure_worktree_is_idempotent_and_pins_base_ref_once

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
