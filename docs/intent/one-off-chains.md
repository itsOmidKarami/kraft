# Intent: one-off chains

An item can be filed with a chain of its own, used by that item only. Content
in it that an administrator did not write runs only once a person has approved
the exact chain that will run. `enforced-by:` names the test that pins each
requirement.

## REQ unapproved-custom-one-off-never-starts

IF a one-off chain holds custom content that no person has approved, THEN the
system SHALL NOT start the work item, whoever asks and however the start is
reached: a resume, a queued or blocked start, or a replayed decision. The item
SHALL stay as it was.
enforced-by: tests/api/test_one_off_approval.py::test_claim_for_run_skips_pending, tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[browser], tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[mcp], tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[worker], tests/api/test_one_off_intake.py::test_custom_one_off_autostart_files_paused
origin: src/kraft/store/work_items.py §claim_for_run -- the one statement every start passes through refuses a pending item, so a start nobody pressed cannot leave it `active` with no walk. `resume_work_item` checks first, to answer with words.

## REQ one-off-approval-is-human-only

The system SHALL record an approval of a one-off chain's custom content only from
a caller it reads as a person, and SHALL refuse one from a Kraft worker or an
MCP session.
enforced-by: tests/api/test_one_off_approval.py::test_approve[mcp], tests/api/test_one_off_intake.py::test_create_approval[mcp]
origin: src/kraft/api/routes/one_off.py §approve_one_off -- the same `gates._decided_by` read that guards `autostart`. A local shell reads as a person too, so this stops sessions and workers, not a determined agent in a shell.

## REQ one-off-approval-binds-the-digest-shown

IF an approval of a one-off chain carries a digest other than that of the chain
as it stands, THEN the system SHALL refuse it as a conflict and SHALL approve
nothing.
enforced-by: tests/api/test_one_off_approval.py::test_approve[stale-digest], tests/api/test_one_off_approval.py::test_approve[ok], tests/api/test_one_off_intake.py::test_create_approval[stale-digest], tests/api/test_one_off_intake.py::test_create_approval[browser]
origin: src/kraft/store/chain.py §approve_one_off -- the digest is compared in the `UPDATE` itself, so a chain replaced between the review and the approval is not approved.

## REQ a-changed-one-off-chain-needs-review-again

WHEN the chain a not-yet-started one-off item will run changes, whatever changed
it, the system SHALL withdraw any approval and SHALL require the review again.
enforced-by: tests/api/test_one_off_approval.py::test_change_reopens_review[draft-after-approval], tests/api/test_one_off_approval.py::test_change_reopens_review[attachment-retrim]
origin: src/kraft/db.py §_WORK_ITEMS_ONE_OFF_REOPEN_TRIGGER -- a trigger on `materialized_chain`, so no writer has to remember: a draft applied, an attachment re-trim and a replacement all reopen it.
