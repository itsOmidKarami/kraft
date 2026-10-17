# Intent: in-place checkout

A repository may run its work items in place, in its own checkout, rather than
each in a git worktree of its own: `worktrees: false` on its `repos.yaml`
entry. An item filed there may say otherwise, and what it is filed as stays
fixed. An in-place item's changes land in the folder the person works in, so
it is never sandboxed.

## REQ in-place-repo-entry-refuses-a-sandbox

IF a repository entry sets `worktrees: false` and a `sandbox`, THEN the system
SHALL refuse the entry.
enforced-by: tests/test_config_repos.py::test_load_repos_rejects_an_entry[in-place-with-a-sandbox], tests/test_config_repos.py::test_load_repos_rejects_an_entry[in-place-with-a-policy-sandbox], tests/api/test_repos_save.py::test_a_save_turning_a_sandboxed_repo_in_place_is_refused
origin: src/kraft/config.py §RepoEntry._in_place_is_never_sandboxed

## REQ in-place-is-frozen-at-intake

WHEN a work item is filed, the system SHALL freeze whether it runs in place
from the item's own flag, else its repository's `worktrees` setting.
enforced-by: tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[repo-default], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[repo-in-place], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[item-in-place], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[item-worktree], tests/templates/test_environment.py::test_an_in_place_target_round_trips_and_an_older_snapshot_reads_as_a_worktree, tests/cli/test_item.py::test_create_sends_whether_the_item_runs_in_place[in-place], tests/cli/test_item.py::test_create_sends_whether_the_item_runs_in_place[worktree], tests/test_mcp.py::test_create_work_item_forwards_the_targets_fields[in-place], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[trigger], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[duplicate-of-in-place], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[duplicate-of-worktree], tests/test_triggers.py::test_a_triggered_item_is_bound_by_its_repositorys_policy[in-place], tests/test_intake_poller.py::test_an_auto_intaken_item_is_bound_by_its_repositorys_policy[in-place]
origin: src/kraft/executor/entry.py §in_place_by

## REQ in-place-refuses-a-sandbox

IF an item would run in place and sandboxed, THEN the system SHALL refuse to
file it and name both settings.
enforced-by: tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[repo-entry], tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[chain], tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[item-policy], tests/executor/test_entry.py::test_a_chain_policy_over_the_ceiling_is_refused_before_any_side_effect[in-place-and-sandboxed]
origin: src/kraft/executor/entry.py §in_place_sandbox_problem

## REQ in-place-refuses-a-workspace

IF a workspace item would run in place, THEN the system SHALL refuse to file
it.
enforced-by: tests/api/test_workspace_intake.py::test_a_workspace_selection_that_cannot_assemble_is_a_422[in-place], tests/api/test_workspace_intake.py::test_a_workspace_selection_that_cannot_assemble_is_a_422[in-an-in-place-root], tests/templates/test_environment.py::test_a_workspace_target_cannot_run_in_place
origin: src/kraft/templates/environment.py §WorkItemTarget._kind_owns_its_fields

## REQ one-in-place-item-works-per-repo

WHILE an in-place item of a repository is active, the system SHALL queue any
other in-place item of that repository that would become active, with a reason
naming the item holding the checkout.
enforced-by: tests/api/test_lifecycle_queue.py::test_resuming_behind_a_working_in_place_item_queues_and_says_who[resume], tests/api/test_lifecycle_queue.py::test_resuming_behind_a_working_in_place_item_queues_and_says_who[retry], tests/api/test_lifecycle_queue.py::test_a_gate_decision_behind_a_working_in_place_item_is_kept_and_goes_on_later[approve], tests/api/test_lifecycle_queue.py::test_a_gate_decision_behind_a_working_in_place_item_is_kept_and_goes_on_later[reject], tests/api/test_lifecycle_queue.py::test_a_held_rejection_paused_and_resumed_by_hand_keeps_its_re_run, tests/api/test_lifecycle_queue.py::test_a_self_retry_queued_for_the_checkout_is_replayed_as_the_agents, tests/api/test_lifecycle_queue.py::test_an_autostart_behind_a_working_in_place_item_is_filed_queued, tests/test_intake.py::test_a_pickup_behind_a_working_in_place_item_is_queued_not_started, tests/executor/test_escalation.py::test_a_self_retry_behind_a_working_in_place_item_is_queued, tests/skills/test_gate_review.py::test_an_agent_verdict_behind_a_working_in_place_item_is_queued[approve], tests/skills/test_gate_review.py::test_an_agent_verdict_behind_a_working_in_place_item_is_queued[reject], tests/store/test_work_items.py::test_the_lease_is_the_folder_not_its_spelling, tests/store/test_work_items.py::test_a_live_escalation_session_holds_the_checkout
origin: src/kraft/store/work_items.py §checkout_holder

## REQ in-place-gate-holds-no-checkout

WHILE an in-place item waits at a gate, is paused, waits, or is stopped, the
system SHALL NOT hold the checkout against another in-place item.
enforced-by: tests/store/test_work_items.py::test_a_parked_in_place_item_holds_no_checkout[needs-human], tests/store/test_work_items.py::test_a_parked_in_place_item_holds_no_checkout[paused], tests/store/test_work_items.py::test_a_parked_in_place_item_holds_no_checkout[waiting], tests/store/test_work_items.py::test_a_parked_in_place_item_holds_no_checkout[at-a-gate]
origin: src/kraft/store/work_items.py §checkout_holder

## REQ worktree-items-ignore-the-checkout-lease

The system SHALL NOT queue a worktree item behind an in-place item's checkout
lease.
enforced-by: tests/store/test_work_items.py::test_a_second_in_place_item_in_one_checkout_is_not_claimed[claim_for_run], tests/store/test_work_items.py::test_a_second_in_place_item_in_one_checkout_is_not_claimed[mark_reentered], tests/store/test_work_items.py::test_a_second_in_place_item_in_one_checkout_is_not_claimed[approve_gate], tests/store/test_work_items.py::test_a_second_in_place_item_in_one_checkout_is_not_claimed[reject_gate]
origin: src/kraft/store/work_items.py §checkout_holder

## REQ in-place-sessions-take-the-lease

WHEN an escalation, review-reply or gate-review agent starts for an in-place
item, the system SHALL take the checkout lease and run the same checks as a
walk task.
enforced-by: tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_waits_for_the_checkout[escalate], tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_waits_for_the_checkout[reply], tests/api/test_lifecycle_queue.py::test_a_checkout_taken_after_the_doors_look_still_gets_no_second_session[escalate], tests/api/test_lifecycle_queue.py::test_a_checkout_taken_after_the_doors_look_still_gets_no_second_session[reply], tests/store/test_session_checkout.py::test_an_agent_outside_the_walk_gets_no_session_beside_a_working_in_place_item[outside-the-walk], tests/store/test_session_checkout.py::test_an_agent_outside_the_walk_gets_no_session_beside_a_working_in_place_item[any-other-session], tests/skills/test_gate_review.py::test_a_review_behind_a_working_in_place_item_leaves_the_gate_to_a_person, tests/test_review_reply.py::test_no_reply_agent_starts_behind_a_working_in_place_item, tests/executor/test_gates_feedback.py::test_a_reply_agent_refused_its_checkout_leaves_the_gate_open_and_says_why, tests/executor/test_escalation.py::test_an_auto_escalation_refused_its_checkout_is_skipped
origin: src/kraft/store/sessions.py §create_session

## REQ in-place-never-forces-a-checkout

The system SHALL NOT run `git checkout -f` in an in-place item's checkout.
enforced-by: tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[tracked], tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[untracked], tests/test_inplace.py::test_take_switches_with_plain_checkout_and_keeps_edits
origin: src/kraft/inplace.py §_take

## REQ in-place-switch-refusal-stops

IF switching the checkout to the item's branch would overwrite uncommitted
changes, THEN the system SHALL stop the item as `checkout` naming the files,
and SHALL leave the changes untouched.
enforced-by: tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[tracked], tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[untracked], tests/executor/test_walk_in_place.py::test_a_refused_take_stops_the_item_as_checkout
origin: src/kraft/inplace.py §_take

## REQ in-place-uncommitted-edits-do-not-stop

WHILE an in-place checkout has uncommitted changes that the switch does not
overwrite, the system SHALL start the item's task without stopping.
enforced-by: tests/test_inplace.py::test_take_switches_with_plain_checkout_and_keeps_edits, tests/test_inplace.py::test_the_first_take_commits_the_attachment_by_path[absent]
origin: src/kraft/inplace.py §take

## REQ in-place-never-runs-setup

The system SHALL NOT run the repository's setup command for an in-place item.
enforced-by: tests/test_inplace.py::test_prepare_never_runs_setup_or_writes_git_config, tests/executor/test_walk_in_place.py::test_a_refused_take_stops_the_item_as_checkout
origin: src/kraft/inplace.py §prepare

## REQ in-place-never-writes-git-config

The system SHALL NOT write the checkout's git config for an in-place item.
enforced-by: tests/test_inplace.py::test_prepare_never_runs_setup_or_writes_git_config
origin: src/kraft/inplace.py §prepare
