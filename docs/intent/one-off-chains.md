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
enforced-by: tests/api/test_one_off_approval.py::test_change_reopens_review[draft-after-approval-paused], tests/api/test_one_off_approval.py::test_change_reopens_review[attachment-retrim-paused], tests/api/test_one_off_approval.py::test_the_walk_stops_an_item_whose_chain_changed_after_the_claim
origin: src/kraft/db.py §_WORK_ITEMS_ONE_OFF_REOPEN_TRIGGER -- a trigger on `materialized_chain` and `run_chain`, so no writer has to remember: a draft applied, an attachment re-trim and a replacement all reopen it. `store.load_chain` refuses an item that reopened after it was claimed, so nothing unreviewed runs from that window.

## REQ claimed-item-chain-is-fixed

WHILE an item is claimed to run and has not loaded its first node, the system
SHALL refuse any change to its chain or documents.
enforced-by: tests/api/test_one_off_approval.py::test_change_reopens_review[draft-after-approval-claimed], tests/api/test_one_off_approval.py::test_change_reopens_review[attachment-retrim-claimed], tests/api/test_one_off_replace.py::test_patch_refuses_a_chain_switch_once_fixed[active-not-started-library], tests/api/test_one_off_replace.py::test_patch_refuses_a_chain_switch_once_fixed[active-not-started-one-off]
origin: src/kraft/store/chain.py §_refuse_if_starting -- `set_attachments` and `revise_chain` refuse that window, so the walk's `load_chain` backstop is left for a crash and no item is stranded `needs_human` with no node.

## REQ duplicate-of-a-one-off-is-unapproved

WHEN a one-off item is duplicated, the system SHALL resolve its authored chain
again as a fresh filing and SHALL file the copy with no approval, so custom
content goes through the review again.
enforced-by: tests/api/test_one_off_replace.py::test_duplicate_one_off[custom], tests/api/test_one_off_replace.py::test_duplicate_one_off[base-gone]
origin: src/kraft/api/routes/work_items.py §duplicate_work_item -- the copy is a new item and approval belongs to one item's chain. A base chain that has gone gives the resolver's 422 rather than a copy of a frozen snapshot.

## REQ agent-text-reaches-no-task-unreviewed

The system SHALL accept, only from a caller it reads as a person, a node
override's `extra_prompt`, a retry's change to a task beyond its model and
effort, and the writing or applying of an item's chain draft, and SHALL refuse
each from a Kraft worker or an MCP session, whether or not the item has started.
enforced-by: tests/api/test_work_items_node_overrides.py::test_a_node_extra_prompt_is_a_persons_to_set[create-assistant], tests/api/test_work_items_node_overrides.py::test_a_node_extra_prompt_is_a_persons_to_set[patch-worker], tests/api/test_retry_paths.py::test_a_retry_that_changes_more_than_a_tasks_model_is_a_persons[escalation-prompt], tests/api/test_retry_paths.py::test_a_retry_that_changes_more_than_a_tasks_model_is_a_persons[assistant-prompt], tests/api/test_item_drafts.py::test_an_items_chain_draft_is_a_persons_to_write_and_apply[assistant-writes-a-prompt], tests/api/test_item_drafts.py::test_an_items_chain_draft_is_a_persons_to_write_and_apply[worker-applies-a-persons-added-node-to-a-started-item]
origin: src/kraft/api/routes/lifecycle.py §_retry, src/kraft/api/routes/work_items.py §_refuse_agent_extra_prompt, src/kraft/api/routes/drafts.py §_person_only -- each door reads `gates._decided_by`, as approving a one-off chain does. Refused rather than held for review: the one-off review covers only an item that has not started, and none of Kraft's own agent flows sends text through these doors. A local shell reads as a person, so this stops sessions and workers, not a determined agent in a shell.

## REQ agent-chosen-repo-commands-wait-for-a-person

WHEN a caller other than a person connects a repository with a test command,
setup command or test scopes of its own, the system SHALL save the repository
disabled, SHALL turn a repository on and set a connected repository's
commands only for a person, and SHALL refuse a caller other than a person that
files work on a disabled repository, or on a workspace that selects a disabled
member, or that starts a never-started item on one.
enforced-by: tests/api/test_repos_held.py::test_a_repo_an_agent_connects_with_its_own_commands_waits_for_a_person[assistant-test-command], tests/api/test_repos_held.py::test_a_repo_an_agent_connects_with_its_own_commands_waits_for_a_person[assistant-asks-to-enable], tests/api/test_repos_held.py::test_turning_a_repo_on_is_a_persons_call[worker-enables], tests/api/test_repos_held.py::test_an_agent_files_no_work_on_a_disabled_repo[assistant-files], tests/api/test_repos_held.py::test_an_agent_files_no_work_on_a_disabled_repo[assistant-triggers], tests/api/test_repos_held.py::test_an_agent_files_no_work_on_a_workspace_with_a_disabled_member[connected-before-its-root], tests/api/test_repos_held.py::test_an_agent_does_not_start_a_persons_item_on_a_disabled_repo[agent-never-started], tests/api/test_repos_held.py::test_turning_a_repo_on_is_a_persons_call[worker-sets-commands-on-a-held-repo]
origin: src/kraft/api/routes/repos.py §add_repo, src/kraft/api/routes/repos.py §update_repo, src/kraft/api/routes/work_items.py §refuse_agent_on_disabled_repo, src/kraft/api/routes/lifecycle.py §resume_work_item -- a repository's commands run for every work item on it, so commands an agent chose wait for a person, who reads them in Settings › Repos. `enabled` alone gates only auto-intake, so the filing refusal is what keeps an agent's own item from running them. A person may still file on a disabled repository by hand. Kraft's own proposal from the repository's files connects as before.
