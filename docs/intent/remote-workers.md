# Remote workers

The other machines Kraft may run steps on, and the connections it keeps to
them: how a machine is registered, how it proves who it is, and what Kraft
tells a person when one is not connected. A connected machine runs the steps
work items place on it.

## REQ machine-has-exactly-one-connection-mode
IF a `workers.yaml` entry names both or neither of `ssh` and `token_sha256`, THEN the system SHALL refuse the file, naming the problem.
enforced-by: tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[neither-mode], tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[both-modes]
origin: src/kraft/remote/registry.py §Worker

## REQ machine-names-cannot-be-placement-values
IF a machine is named `local` or `any`, THEN the system SHALL refuse it.
enforced-by: tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[reserved-any], tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[reserved-local]
origin: src/kraft/remote/registry.py §check_name

## REQ ssh-host-is-never-an-ssh-option
IF an `ssh` value starts with `-` or holds a shell character, THEN the system SHALL refuse it.
enforced-by: tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[ssh-option-injection], tests/remote/test_registry.py::test_a_bad_file_is_refused_naming_the_problem[ssh-shell-metacharacters]
origin: src/kraft/remote/registry.py §_SSH

## REQ dial-in-token-is-stored-hashed-and-shown-once
WHEN a dial-in machine is added, the system SHALL return its token in that response only and store only its SHA-256.
enforced-by: tests/api/test_workers.py::test_adding_a_dial_in_machine_returns_its_token_once_and_stores_only_the_hash
origin: src/kraft/api/routes/workers.py §add_worker

## REQ dial-in-authenticates-by-token-alone
WHEN a machine dials in with a valid token, the system SHALL connect it without a login cookie.
enforced-by: tests/api/test_workers.py::test_a_machine_with_its_token_connects_without_any_login_cookie
origin: src/kraft/api/routes/workers.py §ws_connect

## REQ dial-in-refuses-bad-tokens
IF the token a machine dials in with is wrong or missing, THEN the system SHALL refuse the connection.
enforced-by: tests/api/test_workers.py::test_a_wrong_or_missing_token_is_refused[wrong-token], tests/api/test_workers.py::test_a_wrong_or_missing_token_is_refused[no-token]
origin: src/kraft/api/routes/workers.py §ws_connect

## REQ dial-in-does-not-open-the-perimeter
IF the server has no password, THEN the system SHALL refuse a dial-in from a peer that is not local, token or not.
enforced-by: tests/api/test_workers.py::test_the_route_does_not_open_the_perimeter
origin: src/kraft/api/perimeter.py §_refusal

## REQ incompatible-protocol-is-refused-with-both-versions
IF a machine speaks another protocol version than the server, THEN the system SHALL refuse it, name both versions, and show the machine offline with that reason.
enforced-by: tests/remote/test_protocol.py::test_a_refused_hello_says_why_to_both_sides[newer-machine-names-server-version], tests/remote/test_protocol.py::test_a_refused_hello_says_why_to_both_sides[newer-machine-names-its-own], tests/remote/test_hub.py::test_a_version_mismatch_shows_offline_with_both_versions
origin: src/kraft/remote/protocol.py §server_handshake

## REQ second-connection-replaces-the-first
WHEN a machine connects while it already has a connection, the system SHALL close the old one.
enforced-by: tests/remote/test_hub.py::test_a_second_connection_replaces_the_first
origin: src/kraft/remote/hub.py §_run

## REQ revoking-a-machine-drops-its-connection
WHEN a machine is removed or its credential changes, the system SHALL close its live connection.
enforced-by: tests/remote/test_hub.py::test_removing_a_machine_or_changing_its_token_drops_its_live_connection, tests/remote/test_hub.py::test_a_new_token_hash_drops_the_old_connection
origin: src/kraft/remote/hub.py §apply

## REQ ssh-machines-are-redialled
WHERE a machine is in ssh mode, the system SHALL dial it with the server process's own `ssh` and redial it after the connection ends.
enforced-by: tests/remote/test_hub.py::test_an_ssh_machine_is_dialled_by_the_server_and_redialled_after_it_drops, tests/remote/test_hub.py::test_ssh_that_cannot_run_reports_why_instead_of_hanging
origin: src/kraft/remote/hub.py §_dial_ssh

## REQ machine-redials-until-stopped
WHILE `kraft admin worker serve` runs in dial-in mode, the system SHALL redial after a refusal, a drop or an unreachable server.
enforced-by: tests/remote/test_machine.py::test_a_machine_dials_in_stays_online_and_redials_after_the_server_drops_it, tests/remote/test_machine.py::test_a_wrong_token_is_logged_and_retried_not_fatal, tests/remote/test_machine.py::test_an_unreachable_server_is_logged_and_retried
origin: src/kraft/remote/machine.py §dial_in

## REQ sessions-cannot-manage-machines
IF a Kraft session calls a workers route, THEN the system SHALL refuse it.
enforced-by: tests/api/test_workers.py::test_a_kraft_session_cannot_manage_machines[list], tests/api/test_workers.py::test_a_kraft_session_cannot_manage_machines[add], tests/api/test_workers.py::test_a_kraft_session_cannot_manage_machines[remove], tests/worker/test_callback.py::test_anything_else_is_refused[GET-/api/workers], tests/worker/test_callback.py::test_anything_else_is_refused[POST-/api/workers], tests/worker/test_callback.py::test_anything_else_is_refused[DELETE-/api/workers/gpu-box], tests/worker/test_callback.py::test_anything_else_is_refused[GET-/api/workers/connect]
origin: src/kraft/api/routes/workers.py §_forbid_sessions

## REQ worker-check-fails-on-missing-essentials
IF `git` or every agent CLI is missing, or the server given to `kraft admin worker check` cannot be reached, THEN the system SHALL exit 1, and a missing credential or sandbox runtime SHALL only warn.
enforced-by: tests/remote/test_check.py::test_git_and_one_agent_cli_pass_and_a_missing_credential_only_warns, tests/remote/test_check.py::test_what_must_be_present_fails_when_it_is_not[nothing], tests/remote/test_check.py::test_what_must_be_present_fails_when_it_is_not[no-agent], tests/remote/test_check.py::test_what_must_be_present_fails_when_it_is_not[no-git], tests/remote/test_check.py::test_a_server_that_refuses_the_token_fails_the_server_row, tests/cli/test_worker.py::test_check_exits_1_when_a_row_fails
origin: src/kraft/remote/check.py

## REQ doctor-warns-on-offline-machines
WHEN a registered machine is offline, `kraft admin doctor` SHALL report it with the reason as a warning, not a failure.
enforced-by: tests/test_doctor_workers.py::test_an_offline_machine_is_a_warning_naming_the_reason_not_a_failure, tests/test_doctor_workers.py::test_an_ssh_machine_that_cannot_connect_names_the_servers_own_attempt
origin: src/kraft/doctor.py §_worker_checks

## REQ runner-the-most-specific-setting-wins
WHEN a step is launched, the system SHALL place it by the item's node override, else the item's own override, else the library's step, node or chain `runner:`, else on the server.
enforced-by: tests/remote/test_placement.py::test_the_most_specific_setting_wins[item-beats-library], tests/remote/test_placement.py::test_the_most_specific_setting_wins[node-beats-item], tests/remote/test_placement.py::test_the_most_specific_setting_wins[node-can-force-local], tests/templates/test_runner.py::test_the_most_specific_runner_wins_and_a_flat_task_list_is_one_step, tests/templates/test_runner.py::test_whatever_a_node_owns_takes_its_placement
origin: src/kraft/remote/placement.py §runner_for

## REQ runner-refuses-a-machine-it-cannot-use
IF an item names a machine that is not in `workers.yaml` or not allowed for its repo, THEN the system SHALL refuse it with a 422 naming the machine.
enforced-by: tests/remote/test_placement.py::test_a_machine_that_is_unknown_or_not_allowed_for_the_repo_is_refused[unknown-machine], tests/remote/test_placement.py::test_a_machine_that_is_unknown_or_not_allowed_for_the_repo_is_refused[repo-not-allowed], tests/api/test_runner_overrides.py::test_a_placement_that_cannot_work_is_a_422_that_says_why[unknown-machine]
origin: src/kraft/remote/placement.py §refusal

## REQ runner-refuses-items-a-machine-cannot-take
IF an item with a machine runner is in place, sandboxed, or has workspace members, submodules or Git LFS, THEN the system SHALL refuse it, naming both settings.
enforced-by: tests/remote/test_placement.py::test_filing_problems_name_both_settings_for_in_place_and_sandbox, tests/remote/test_placement.py::test_a_checkout_a_single_bundle_cannot_carry_is_named[submodule-submodules], tests/remote/test_placement.py::test_a_checkout_a_single_bundle_cannot_carry_is_named[lfs-Git LFS], tests/adapters/test_subprocess_remote.py::test_a_placement_that_cannot_hold_is_a_config_error_naming_why_and_runs_nothing[unknown-machine], tests/adapters/test_subprocess_remote.py::test_a_placement_that_cannot_hold_is_a_config_error_naming_why_and_runs_nothing[sandboxed-item]
origin: src/kraft/remote/placement.py §filing_problems

## REQ any-never-means-the-server
WHERE a step's runner is `any`, the system SHALL choose the first connected machine that may work on every repo of the item and has a free slot, and SHALL NOT run the step on the server.
enforced-by: tests/remote/test_hub.py::test_any_takes_the_first_connected_allowed_machine_with_a_free_slot_never_the_laptop, tests/executor/test_remote_walk.py::test_any_picks_the_connected_allowed_machine_and_never_the_server
origin: src/kraft/remote/hub.py §decide

## REQ machine-capacity-bounds-its-sessions
WHILE a machine runs as many sessions as its capacity, the system SHALL NOT start another on it.
enforced-by: tests/remote/test_hub.py::test_a_named_machine_offline_or_full_makes_the_step_wait_and_never_run_elsewhere, tests/remote/test_hub.py::test_a_waiting_claim_is_granted_when_a_slot_frees_and_is_cancellable
origin: src/kraft/remote/hub.py §claim

## REQ step-waits-for-an-offline-or-full-machine
WHILE the machine a step is placed on is offline or full, the system SHALL park the step as waiting with no session row, SHALL NOT run it elsewhere, and SHALL NOT count the wait against its time cap.
enforced-by: tests/executor/test_dispatch_remote.py::test_a_step_on_an_offline_machine_parks_the_item_and_runs_when_it_returns, tests/adapters/test_subprocess_remote.py::test_a_step_waiting_for_a_full_machine_has_no_session_row_and_never_runs_elsewhere, tests/adapters/test_subprocess_remote.py::test_a_wait_moves_the_time_cap_on_by_however_long_it_lasted
origin: src/kraft/executor/dispatch.py §_dispatch_task

## REQ remote-session-records-its-machine
WHEN a step runs on a machine, the system SHALL record the machine on its session and keep the stand-in as the session's process.
enforced-by: tests/adapters/test_subprocess_remote.py::test_a_remote_step_is_a_session_row_with_its_machine_and_the_standin_as_its_process, tests/test_db_migrations_sessions.py::test_migrate_v56_to_v57_adds_a_nullable_runner_column
origin: src/kraft/adapters/subprocess.py §_run_task

## REQ remote-step-gets-only-the-commits-the-machine-lacks
WHEN a remote step launches, the system SHALL send the branch head as a bundle that excludes every commit the machine already has.
enforced-by: tests/remote/test_bundle.py::test_a_first_bundle_carries_the_whole_branch_and_a_later_one_only_what_is_new, tests/remote/test_runs.py::test_the_second_step_is_sent_only_what_the_machine_lacks_and_sees_the_first_steps_work
origin: src/kraft/remote/runs.py §_send_job

## REQ remote-job-carries-no-server-environment-or-credentials
The system SHALL NOT send the server's environment or credentials to a machine; the command runs with the machine's own.
enforced-by: tests/remote/test_jobs.py::test_nothing_of_the_servers_own_environment_or_credentials_reaches_the_command, tests/remote/test_jobs.py::test_the_server_paths_in_argv_and_env_become_the_machines
origin: src/kraft/remote/jobs.py

## REQ remote-log-resumes-from-the-servers-offset
WHEN a machine reconnects mid-step, the system SHALL resume the log from the byte count the server has, with nothing doubled or missing.
enforced-by: tests/remote/test_runs.py::test_the_log_resumes_byte_for_byte_after_the_connection_is_cut_mid_step, tests/remote/test_machine.py::test_a_finished_job_is_resent_from_the_offset_the_server_names
origin: src/kraft/remote/runs.py §_log

## REQ remote-pause-and-cancel-cross-the-stand-in
WHEN a remote step is paused, the system SHALL send SIGINT to its command, and WHEN it is cancelled, kill it; the stand-in exits with the command's exit code.
enforced-by: tests/remote/test_listener.py::test_sigint_and_sigterm_cross_to_the_machine[pause-sends-int], tests/remote/test_listener.py::test_sigint_and_sigterm_cross_to_the_machine[cancel-sends-kill], tests/remote/test_listener.py::test_the_standin_writes_the_machines_log_to_its_stdout_and_exits_with_the_remote_code
origin: src/kraft/remote/standin.py

## REQ remote-result-is-home-before-the-step-is-over
WHEN a remote step ends, the system SHALL take its commits, result and `docs/` changes in before the stand-in exits.
enforced-by: tests/remote/test_runs.py::test_a_step_runs_on_the_machine_and_its_result_comes_home_before_it_is_over
origin: src/kraft/remote/runs.py §_deliver

## REQ remote-commits-publish-by-compare-and-swap
WHEN a remote step's commits arrive and the item branch moved meanwhile, the system SHALL keep them at `refs/kraft/unsynced/…`, record `sandbox_branch_not_synced` and leave the branch as it is.
enforced-by: tests/remote/test_bundle.py::test_a_branch_that_moved_meanwhile_keeps_the_commits_unsynced_and_is_not_overwritten, tests/remote/test_runs.py::test_a_branch_that_moved_while_the_step_ran_is_kept_unsynced_and_not_overwritten
origin: src/kraft/remote/bundle.py §publish

## REQ remote-bundle-may-not-plant-a-repository
IF a machine's commits add a nested git repository or do not continue from the head it was sent, THEN the system SHALL fail the step with the reason in its log and leave the branch and worktree untouched.
enforced-by: tests/remote/test_bundle.py::test_a_bundle_that_plants_a_repository_is_refused_and_leaves_the_branch_alone, tests/remote/test_bundle.py::test_a_bundle_that_is_not_the_item_branch_or_does_not_continue_from_the_base_is_refused, tests/remote/test_runs.py::test_a_bundle_that_plants_a_repository_fails_the_step_and_touches_nothing
origin: src/kraft/remote/bundle.py §fetch

## REQ remote-docs-conflict-keeps-the-servers-version
IF the server's copy of a `docs/` file changed since the package left, THEN the system SHALL keep the server's version, save the step's copy in the session's folder, and record `remote_docs_conflict` naming the file.
enforced-by: tests/remote/test_files.py::test_a_document_a_person_edited_meanwhile_keeps_the_servers_version_and_saves_the_steps, tests/remote/test_runs.py::test_a_document_a_person_edited_while_the_step_ran_keeps_the_servers_version
origin: src/kraft/remote/files.py §apply_returned

## REQ remote-api-calls-are-held-to-the-sessions-allowlist
WHEN a remote agent calls Kraft, the system SHALL answer only what `worker/callback.py` allows for that session's own work item, as the connection's session, and SHALL refuse the rest.
enforced-by: tests/remote/test_runs.py::test_the_agents_kraft_calls_cross_as_requests_held_to_its_own_sessions_allowlist[its-own-item], tests/remote/test_runs.py::test_the_agents_kraft_calls_cross_as_requests_held_to_its_own_sessions_allowlist[another-item], tests/remote/test_runs.py::test_the_agents_kraft_calls_cross_as_requests_held_to_its_own_sessions_allowlist[machine-management]
origin: src/kraft/remote/runs.py §_api

## REQ remote-step-without-its-cli-or-setup-does-not-start
IF the agent CLI is missing on the machine or its setup command fails, THEN the system SHALL NOT start the step and SHALL end it as `config_error` or failed with the reason in its log.
enforced-by: tests/remote/test_jobs.py::test_a_missing_agent_cli_never_starts_and_says_which, tests/remote/test_jobs.py::test_a_failing_setup_stops_the_step_before_the_command, tests/remote/test_listener.py::test_a_config_error_from_the_machine_is_exit_code_78_with_the_reason_in_the_log
origin: src/kraft/remote/jobs.py

## REQ remote-step-survives-a-server-restart
WHEN the server restarts under a running remote step, the system SHALL re-pair the step with its stand-in and finish its log without a gap, and WHEN the stand-in died with the server, start a new one that resumes at the log file's size.
enforced-by: tests/remote/test_restart.py::test_a_step_survives_the_server_restarting_and_its_log_is_whole, tests/worker/test_reattach_remote.py::test_a_remote_session_whose_standin_died_gets_a_new_one_that_resumes_at_the_logs_size
origin: src/kraft/worker/reattach.py

## REQ machine-survives-ssh-drops
WHEN the ssh session to a machine ends, the system SHALL keep the machine's running steps and their state in the machine daemon, and WHEN a new session connects, hand the same steps back to it with their logs resuming from the server's offset.
enforced-by: tests/remote/test_daemon.py::test_a_job_outlives_the_relay_that_brought_it, tests/remote/test_daemon.py::test_a_second_relay_starts_no_second_daemon, tests/remote/test_daemon.py::test_the_socket_is_this_users_only
origin: src/kraft/remote/daemon.py

## REQ offline-calls-wait-for-the-connection
WHILE a machine has no connection, the system SHALL hold the agent's calls to Kraft until the connection returns, and WHEN the connection drops after a call was sent, SHALL answer that call 503 and SHALL NOT send it again.
enforced-by: tests/remote/test_machine.py::test_a_call_made_while_offline_is_sent_and_answered_after_reconnect, tests/remote/test_machine.py::test_a_call_in_flight_when_the_connection_drops_is_told_so
origin: src/kraft/remote/machine.py §_forward

## REQ machine-gives-up-after-give-up-after
WHEN a machine has had no connection for its `give_up_after` of awake time, the system SHALL stop the steps running on it and write why at the end of each log, and WHERE `give_up_after` is `never`, SHALL NOT stop them.
enforced-by: tests/remote/test_machine.py::test_a_machine_without_kraft_for_give_up_after_stops_its_jobs, tests/remote/test_machine.py::test_never_keeps_a_job_running_however_long_kraft_is_gone, tests/remote/test_machine.py::test_a_reconnect_before_give_up_after_keeps_the_job, tests/remote/test_hub.py::test_the_welcome_tells_a_machine_its_own_give_up_after[30m-1800.0], tests/remote/test_hub.py::test_the_welcome_tells_a_machine_its_own_give_up_after[never-None]
origin: src/kraft/remote/machine.py §_give_up

## REQ server-gives-up-on-awake-time
WHEN a machine has been out of reach for its `give_up_after` of time the server was awake, the system SHALL end each step on it as `unknown`, record `remote_step_given_up`, and discard whatever the machine hands in later, and WHERE `give_up_after` is `never`, SHALL NOT.
enforced-by: tests/remote/test_hub.py::test_offline_for_counts_from_the_drop_on_the_monotonic_clock, tests/remote/test_runs.py::test_a_run_whose_machine_is_gone_give_up_after_ends_unknown_and_kills_it_on_reconnect, tests/remote/test_runs.py::test_never_does_not_give_up, tests/adapters/test_subprocess_remote.py::test_a_step_given_up_on_ends_its_session_unknown
origin: src/kraft/remote/runs.py §watch

## REQ removed-machine-gives-up-its-runs
WHEN a machine is removed from workers.yaml, the system SHALL give up every unfinished step on it at once.
enforced-by: tests/remote/test_runs.py::test_removing_a_machine_gives_up_its_runs_at_once, tests/api/test_workers.py::test_removing_a_machine_gives_up_the_runs_it_held
origin: src/kraft/api/routes/workers.py

## REQ machine-enforces-the-steps-caps
WHILE a step runs on a machine, the system SHALL stop it there when its time cap or idle limit is reached, and WHEN the machine reports that, SHALL end the session `capped_out` with the same `time_cap_reached` event a server-side cap writes.
enforced-by: tests/remote/test_jobs.py::test_the_time_cap_stops_the_command_on_the_machine, tests/remote/test_jobs.py::test_a_command_whose_log_stays_silent_past_idle_s_is_stopped, tests/adapters/test_subprocess_remote.py::test_a_cap_the_machine_enforced_ends_the_session_time_capped
origin: src/kraft/remote/jobs.py §_until_exit

## REQ machine-forgets-gone-items
WHEN an item is archived or abandoned, and on every reconnect for each item the server no longer lists, the system SHALL remove the machine's copy of the item, and SHALL NOT remove it while one of the item's steps is running there.
enforced-by: tests/remote/test_machine.py::test_forget_removes_an_items_copy_but_not_one_that_is_running[finished], tests/remote/test_machine.py::test_forget_removes_an_items_copy_but_not_one_that_is_running[running], tests/remote/test_machine.py::test_keep_removes_every_item_the_server_no_longer_lists, tests/remote/test_runs.py::test_a_reconnect_tells_the_machine_which_items_to_keep, tests/api/test_archive.py::test_archiving_or_abandoning_tells_the_machines_to_forget_the_item[archive], tests/api/test_archive.py::test_archiving_or_abandoning_tells_the_machines_to_forget_the_item[abandon]
origin: src/kraft/remote/machine.py §_forget_items

## REQ session-names-its-machine-and-reachability
The system SHALL name the machine a session ran on, and WHILE a session still running or paused is on a machine that is out of reach, SHALL say since when.
enforced-by: tests/api/test_board.py::test_a_live_session_on_a_machine_that_is_out_of_reach_says_since_when[running-on-a-dropped-machine], tests/api/test_board.py::test_a_live_session_on_a_machine_that_is_out_of_reach_says_since_when[finished-session], tests/api/test_board.py::test_a_live_session_on_a_machine_that_is_out_of_reach_says_since_when[local-session], tests/remote/test_hub.py::test_unreachable_since_names_the_moment_the_connection_ended_until_it_is_back, tests/cli/test_watching.py::test_logs_name_the_machine_a_remote_session_ran_on[remote]
origin: src/kraft/api/routes/board.py §_sessions_out

## REQ settings-lists-machines-read-only
The system SHALL list the registered machines in Settings with their state, and SHALL NOT offer to add, change or remove one there.
enforced-by: frontend/src/ng/settings/WorkersPage.test.tsx::Settings › Workers, tests/api/test_api_shapes.py::test_the_table_is_what_the_server_returns
origin: frontend/src/ng/settings/WorkersPage.tsx

## REQ doctor-names-a-chain-on-an-unknown-machine
IF a chain places a step on a machine that is not in workers.yaml, THEN doctor SHALL fail its chains row naming the machine.
enforced-by: tests/test_doctor_workers.py::test_a_chain_placed_on_a_machine_workers_yaml_lacks_fails_the_chains_row
origin: src/kraft/doctor.py §_chains_check

## REQ item-history-records-machine-drops
WHEN a machine loses or regains its connection, the system SHALL record that on each item with an unfinished step on it, and on no other.
enforced-by: tests/remote/test_runs.py::test_a_drop_and_a_reconnect_each_note_the_item_whose_step_runs_there
origin: src/kraft/remote/runs.py §_note_machine
