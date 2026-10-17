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
enforced-by: tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_waits_for_the_checkout[escalate], tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_waits_for_the_checkout[reply], tests/api/test_lifecycle_queue.py::test_a_checkout_taken_after_the_doors_look_still_gets_no_second_session[escalate], tests/api/test_lifecycle_queue.py::test_a_checkout_taken_after_the_doors_look_still_gets_no_second_session[reply], tests/store/test_session_checkout.py::test_an_agent_outside_the_walk_gets_no_session_beside_a_working_in_place_item[outside-the-walk], tests/store/test_session_checkout.py::test_an_agent_outside_the_walk_gets_no_session_beside_a_working_in_place_item[any-other-session], tests/skills/test_gate_review.py::test_a_review_behind_a_working_in_place_item_leaves_the_gate_to_a_person[another-item-uses-it], tests/test_review_reply.py::test_no_reply_agent_starts_without_its_checkout[another-item-uses-it], tests/executor/test_gates_feedback.py::test_a_reply_agent_refused_its_checkout_leaves_the_gate_open_and_says_why[another-item-uses-it], tests/executor/test_escalation.py::test_an_auto_escalation_refused_its_checkout_is_skipped[another-item-uses-it], tests/adapters/test_agent_in_place.py::test_an_in_place_session_outside_the_walk_takes_marks_and_checks_its_checkout[overlap], tests/adapters/test_agent_in_place.py::test_an_in_place_session_outside_the_walk_takes_marks_and_checks_its_checkout[moved], tests/adapters/test_agent_in_place.py::test_an_in_place_session_outside_the_walk_takes_marks_and_checks_its_checkout[leftover], tests/adapters/test_agent_in_place.py::test_an_in_place_session_refused_its_checkout_never_launches, tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_refused_its_checkout_stops_the_item_as_checkout[escalate], tests/api/test_lifecycle_queue.py::test_a_session_outside_the_walk_refused_its_checkout_stops_the_item_as_checkout[reply], tests/test_review_reply.py::test_no_reply_agent_starts_without_its_checkout[it-cannot-be-taken], tests/skills/test_gate_review.py::test_a_review_behind_a_working_in_place_item_leaves_the_gate_to_a_person[it-cannot-be-taken], tests/executor/test_gates_feedback.py::test_a_reply_agent_refused_its_checkout_leaves_the_gate_open_and_says_why[it-cannot-be-taken], tests/executor/test_escalation.py::test_an_auto_escalation_refused_its_checkout_is_skipped[it-cannot-be-taken], tests/skills/test_gate_review.py::test_a_reviewer_that_moves_the_checkout_stops_the_item_and_its_verdict_is_dropped, tests/skills/test_gate_review.py::test_no_reviewer_starts_at_a_gate_its_checkout_stopped, tests/executor/test_gates_feedback.py::test_a_reply_agents_checkout_stop_ends_the_walk_at_the_gate, tests/worker/test_reattach_sweep.py::test_a_reattached_in_place_escalation_is_checked_before_its_self_retry[adopted], tests/worker/test_reattach_sweep.py::test_a_reattached_in_place_escalation_is_checked_before_its_self_retry[dead], tests/executor/test_dispatch_in_place.py::test_a_checkout_stop_from_a_session_outside_the_walk_keeps_no_node_task[escalation], tests/executor/test_dispatch_in_place.py::test_a_checkout_stop_from_a_session_outside_the_walk_keeps_no_node_task[implementation.reviewer]
origin: src/kraft/store/sessions.py §create_session

## REQ in-place-never-forces-a-checkout

The system SHALL NOT run `git checkout -f` in an in-place item's checkout.
enforced-by: tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[tracked], tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[untracked], tests/test_inplace.py::test_take_switches_with_plain_checkout_and_keeps_edits[plain], tests/executor/test_walk_in_place.py::test_no_sweep_after_an_in_place_task_moves_or_commits_anything, tests/test_inplace_rebase.py::test_a_rebase_in_the_checkout_refuses_what_it_would_force[ignored-file-the-base-tracks], tests/test_inplace_rebase.py::test_a_rebase_in_the_checkout_refuses_what_it_would_force[ignored-file-the-branch-untracked], tests/test_inplace_rebase.py::test_a_rebase_in_the_checkout_refuses_what_it_would_force[ignored-file-a-branch-commit-wrote], tests/test_inplace_rebase.py::test_a_rebase_in_the_checkout_refuses_what_it_would_force[the-persons-merge], tests/test_inplace_rebase.py::test_a_rebase_in_the_checkout_refuses_what_it_would_force[unmerged-path], tests/test_inplace_rebase.py::test_a_failed_rebase_never_aborts_the_persons_own
origin: src/kraft/inplace.py §_take

## REQ in-place-switch-refusal-stops

IF switching the checkout to the item's branch would overwrite uncommitted
changes, THEN the system SHALL stop the item as `checkout` naming the files,
and SHALL leave the changes untouched.
enforced-by: tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[tracked], tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[untracked], tests/test_inplace.py::test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing[ignored], tests/executor/test_walk_in_place.py::test_a_refused_take_stops_the_item_as_checkout
origin: src/kraft/inplace.py §_take

## REQ in-place-uncommitted-edits-do-not-stop

WHILE an in-place checkout has uncommitted changes that the switch does not
overwrite, the system SHALL start the item's task without stopping.
enforced-by: tests/test_inplace.py::test_take_switches_with_plain_checkout_and_keeps_edits[plain], tests/test_inplace.py::test_the_first_take_commits_the_attachment_by_path[absent], tests/executor/test_walk_in_place.py::test_an_agent_task_runs_on_the_items_branch[the-persons-edit]
origin: src/kraft/inplace.py §take

## REQ in-place-never-runs-setup

The system SHALL NOT run the repository's setup command for an in-place item.
enforced-by: tests/test_inplace.py::test_prepare_never_runs_setup_or_writes_git_config, tests/executor/test_walk_in_place.py::test_a_refused_take_stops_the_item_as_checkout
origin: src/kraft/inplace.py §prepare

## REQ in-place-never-writes-git-config

The system SHALL NOT write the checkout's git config for an in-place item.
enforced-by: tests/test_inplace.py::test_prepare_never_runs_setup_or_writes_git_config
origin: src/kraft/inplace.py §prepare

## REQ in-place-never-sweeps-stragglers

WHEN an in-place item's task ends, the system SHALL NOT commit files the task
left uncommitted.
enforced-by: tests/executor/test_dispatch_in_place.py::test_an_edited_file_the_agent_never_touched_stays_uncommitted_and_unstopped, tests/executor/test_walk_in_place.py::test_no_sweep_after_an_in_place_task_moves_or_commits_anything, tests/worker/test_reattach_sweep.py::test_a_reattached_in_place_task_is_checked_not_swept[overlap]
origin: src/kraft/executor/dispatch.py §sweep_stragglers

## REQ in-place-records-leftovers

WHEN an in-place item's task ends with uncommitted paths that were not dirty
when it started, the system SHALL record those paths on the item's history.
enforced-by: tests/executor/test_dispatch_in_place.py::test_an_edited_file_the_agent_never_touched_stays_uncommitted_and_unstopped, tests/test_inplace.py::test_check_leftovers_are_only_what_the_task_left[plain]
origin: src/kraft/inplace.py §check_task

## REQ in-place-moved-branch-stops

IF an in-place checkout is not on the item's branch when a task ends, THEN the
system SHALL stop the item as `checkout`, list the task's commits not on the
item's branch, and SHALL NOT switch the branch back.
enforced-by: tests/executor/test_dispatch_in_place.py::test_a_branch_switch_mid_task_stops_and_never_switches_back[stays-away], tests/executor/test_dispatch_in_place.py::test_a_branch_switch_mid_task_stops_and_never_switches_back[switched-back], tests/test_inplace.py::test_check_names_a_moved_checkout_and_the_commits_made_off_the_branch, tests/worker/test_reattach_sweep.py::test_a_reattached_in_place_task_is_checked_not_swept[moved-with-a-stray-commit], tests/executor/test_dispatch_in_place.py::test_resume_after_a_checkout_stop_reruns_the_task_only_for_stray_commits[stray-commit-reruns]
origin: src/kraft/inplace.py §check_task

## REQ in-place-overlap-stops

IF an in-place task's commits touch a path that had uncommitted changes when
the task started, THEN the system SHALL stop the item as `checkout` and name
the paths.
enforced-by: tests/executor/test_dispatch_in_place.py::test_the_agent_committing_a_persons_edited_file_stops_as_checkout[done], tests/executor/test_dispatch_in_place.py::test_the_agent_committing_a_persons_edited_file_stops_as_checkout[paused-as-it-ended], tests/executor/test_dispatch_in_place.py::test_a_large_dirty_set_is_sampled_on_the_event_and_kept_whole_in_a_file, tests/worker/test_reattach_sweep.py::test_a_reattached_in_place_task_is_checked_not_swept[overlap], tests/test_inplace.py::test_check_overlap_is_the_tasks_own_commits_not_a_merged_main[commit], tests/test_inplace.py::test_check_overlap_is_the_tasks_own_commits_not_a_merged_main[merge]
origin: src/kraft/inplace.py §check

## REQ in-place-leftovers-stay-the-items

WHEN a later task of an in-place item commits a path an earlier task left
uncommitted and its content is unchanged since, the system SHALL NOT stop the
item for overlap.
enforced-by: tests/executor/test_dispatch_in_place.py::test_a_later_task_committing_an_earlier_tasks_leftover_does_not_stop, tests/test_inplace.py::test_mark_counts_the_persons_edits_and_not_the_items_own_leftovers
origin: src/kraft/inplace.py §mark

## REQ in-place-checkout-stop-never-auto-escalates

The system SHALL NOT auto-escalate a `checkout` stop.
enforced-by: tests/executor/test_dispatch_in_place.py::test_a_checkout_stop_is_never_auto_escalated, tests/executor/test_escalation.py::test_resume_after_escalation_drops_a_self_retry_on_a_moved_item[checkout-stop]
origin: src/kraft/executor/walk.py §_sentinel_stop

## REQ in-place-rebase-keeps-edits

WHEN an in-place item's branch is rebased with uncommitted changes in the
checkout, the system SHALL stash them for the rebase and apply them back
afterwards.
enforced-by: tests/test_inplace_rebase.py::test_rebase_in_the_checkout_keeps_the_persons_edits, tests/test_inplace_rebase.py::test_a_conflicting_rebase_in_the_checkout_aborts_and_puts_the_edits_back, tests/test_inplace_rebase.py::test_a_cancel_mid_rebase_in_the_checkout_waits_for_git[clean]
origin: src/kraft/inplace.py §rebase_in_checkout

## REQ in-place-autostash-clash-stops

IF applying the stashed changes back after an in-place rebase leaves unmerged
paths, THEN the system SHALL stop the item as `checkout`, name the paths, and
record the stash's commit, and SHALL NOT drop the stash.
enforced-by: tests/test_inplace_rebase.py::test_an_autostash_clash_raises_with_the_stash_and_leaves_it, tests/test_inplace_rebase.py::test_an_autostash_clash_stops_the_item_as_checkout[resume-autostash-clash], tests/test_inplace_rebase.py::test_an_autostash_clash_stops_the_item_as_checkout[retry-autostash-clash], tests/test_inplace_rebase.py::test_an_autostash_clash_stops_the_item_as_checkout[resume-clash-as-a-pause-lands], tests/test_inplace_rebase.py::test_a_cancel_mid_rebase_in_the_checkout_waits_for_git[clash-named-over-the-cancel], tests/api/test_gates_refresh.py::test_an_in_place_refresh_that_clashes_stops_the_approved_item_as_checkout, tests/executor/test_escalation.py::test_a_self_retry_whose_in_place_rebase_clashes_stops_as_checkout, tests/executor/test_mr_rebase_dispatch.py::test_an_in_place_rebase_that_clashes_is_a_checkout_stop, tests/adapters/forge/test_waits.py::test_an_in_place_conflict_rebase_that_clashes_stops_the_item_as_checkout[ci_poll]
origin: src/kraft/inplace.py §rebase_in_checkout

## REQ in-place-git-only-rebase-leaves-the-checkout

WHEN an in-place item's branch is rebased with no agent task following and the
checkout is not on that branch, the system SHALL rebase it outside the checkout
and SHALL NOT change the checkout's HEAD or files.
enforced-by: tests/test_inplace_rebase.py::test_rebase_off_the_branch_never_moves_the_checkout[rebased], tests/test_inplace_rebase.py::test_rebase_off_the_branch_never_moves_the_checkout[conflict], tests/test_inplace_rebase.py::test_rebase_off_the_branch_never_moves_the_checkout[after-a-killed-server], tests/test_inplace_rebase.py::test_a_slow_throwaway_checkout_does_not_hold_up_the_server, tests/test_builtins_rebase.py::test_mr_rebase_of_an_in_place_item_never_moves_the_checkout
origin: src/kraft/inplace.py §rebase

## REQ in-place-mr-names-its-branch

WHEN an in-place item opens or updates its merge request, the system SHALL name
the item's branch to the forge rather than read the checkout's current branch.
enforced-by: tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-open_mr], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-open_mr], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-update_mr], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-update_mr], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-mark_ready], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-mark_ready], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-ci_status], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-ci_status], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-merge], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-merge], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-set_labels], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-set_labels], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-bot_review], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-bot_review], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[glab-check_review], tests/adapters/forge/test_forge_cli_contract.py::test_every_merge_request_call_names_the_branch[gh-check_review], tests/adapters/forge/test_glab.py::test_glab_ci_status_only_trusts_a_pipeline_for_the_current_head[not-the-checkouts-head], tests/adapters/forge/test_git.py::test_open_mr_in_place_ignores_a_dirty_checkout, tests/adapters/forge/test_git.py::test_merge_accepts_a_pushed_head_against_real_git[checkout-elsewhere], tests/executor/test_dispatch_in_place.py::test_open_mr_refuses_a_dirty_folder_only_for_a_worktree_item[in-place], tests/adapters/forge/test_run_chain.py::test_open_mr_hands_the_forge_its_clean_check[in-place]
origin: src/kraft/adapters/forge/mr.py §target

## REQ base-prefers-the-default-branch-ref-over-head

WHEN an item starts with no fetched `origin`, the system SHALL base it on the
default branch's ref, and SHALL read the checkout's HEAD only where that ref
does not exist.
enforced-by: tests/test_base_branch.py::test_the_base_fallback_reads_the_default_branch_ref_not_head[on-an-items-branch]
origin: src/kraft/builtins.py §upstream_head
