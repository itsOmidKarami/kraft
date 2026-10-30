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
enforced-by: tests/api/test_one_off_approval.py::test_claim_for_run_skips_pending, tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[browser], tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[mcp], tests/api/test_one_off_approval.py::test_resume_refuses_unapproved_custom[worker], tests/api/test_one_off_intake.py::test_custom_one_off_autostart_files_paused[free], tests/api/test_one_off_intake.py::test_custom_one_off_autostart_files_paused[when-free-hold]
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
enforced-by: tests/api/test_one_off_approval.py::test_approve_refuses_a_chain_changed_since_the_review, tests/api/test_one_off_approval.py::test_approve[stale-digest], tests/api/test_one_off_approval.py::test_approve[ok], tests/api/test_one_off_intake.py::test_create_approval[stale-digest], tests/api/test_one_off_intake.py::test_create_approval[browser], tests/api/test_one_off_intake.py::test_a_library_reload_between_review_and_create_is_refused
origin: src/kraft/store/chain.py §approve_one_off -- the review digest covers the authored chain and the chains that will run (`review_digest`), and is compared in the same transaction as the write, so a replacement, a draft or a re-trim between the review and the approval is not approved.

## REQ a-changed-one-off-chain-needs-review-again

WHEN the chain a not-yet-started one-off item will run changes, whatever changed
it, the system SHALL withdraw any approval and SHALL require the review again.
enforced-by: tests/api/test_one_off_approval.py::test_change_reopens_review[draft-after-approval], tests/api/test_one_off_approval.py::test_change_reopens_review[attachment-retrim], tests/api/test_one_off_approval.py::test_the_walk_stops_an_item_whose_chain_changed_after_the_claim
origin: src/kraft/db.py §_WORK_ITEMS_ONE_OFF_REOPEN_TRIGGER -- a trigger on `materialized_chain` and `run_chain`, so no writer has to remember: a draft applied, an attachment re-trim and a replacement all reopen it. `store.load_chain` refuses an item that reopened after it was claimed, so nothing unreviewed runs from that window.

## REQ duplicate-of-a-one-off-is-unapproved

WHEN a one-off item is duplicated, the system SHALL resolve its authored chain
again as a fresh filing and SHALL file the copy with no approval, so custom
content goes through the review again.
enforced-by: tests/api/test_one_off_replace.py::test_duplicate_one_off[custom], tests/api/test_one_off_replace.py::test_duplicate_one_off[base-gone]
origin: src/kraft/api/routes/work_items.py §duplicate_work_item -- the copy is a new item and approval belongs to one item's chain. A base chain that has gone gives the resolver's 422 rather than a copy of a frozen snapshot.
