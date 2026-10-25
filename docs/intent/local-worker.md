# The local worker

The process beside the server that starts every local session: an agent session
or a command. The server builds the launch and goes on supervising the session
by its pid and by the files it writes; the worker starts it, reaps it, remembers
it and says whether it still holds it.

## REQ local-session-is-started-by-the-worker
WHEN a local session starts, the system SHALL have the local worker start it and SHALL NOT start it in the server's own process.
enforced-by: tests/adapters/test_subprocess_local.py::test_local_session_is_started_by_the_worker, tests/adapters/test_subprocess_local.py::test_no_link_installed_is_a_config_error
origin: src/kraft/adapters/subprocess.py §_start_locally

## REQ worker-starts-the-launch-as-given
WHEN the server asks the worker to start a session, the worker SHALL run the given command in the given directory with exactly the given environment, as a process group of its own, appending to the given log.
enforced-by: tests/remote/test_local_jobs.py::test_spawn_runs_the_argv_with_exactly_the_given_env, tests/remote/test_local_serve.py::test_spawn_frame_starts_the_session_as_the_workers_child
origin: src/kraft/remote/local_jobs.py

## REQ worker-refuses-a-launch-that-cannot-start
IF the working directory is missing or the process cannot be started, THEN the worker SHALL refuse the launch, saying which, and SHALL keep serving.
enforced-by: tests/remote/test_local_serve.py::test_refused_frames[missing-working-directory], tests/remote/test_local_serve.py::test_refused_frames[cwd-is-a-file], tests/adapters/test_subprocess_local.py::test_a_session_the_worker_refuses_is_a_config_error[cwd]
origin: src/kraft/remote/local_serve.py

## REQ worker-adopts-a-session-by-pid-identity
WHEN asked about a session it did not start, the worker SHALL hold it only if its pid is still the process recorded, by start time, and SHALL NOT hold a pid another process has reused.
enforced-by: tests/remote/test_local_jobs.py::test_hold_adopts_an_unknown_live_process_only_by_identity[same-process-adopted], tests/remote/test_local_jobs.py::test_hold_adopts_an_unknown_live_process_only_by_identity[pid-reused-by-another-process]
origin: src/kraft/remote/local_jobs.py §hold

## REQ restarted-worker-holds-the-sessions-its-predecessor-left
WHEN the worker starts, it SHALL hold the recorded sessions that are still the processes recorded and drop the rest.
enforced-by: tests/remote/test_local_jobs.py::test_recover_keeps_only_live_identities, tests/remote/test_local_link.py::test_link_reconnects_after_the_worker_dies, tests/remote/test_local_worker_process.py::test_a_session_outlives_the_worker_and_the_next_worker_holds_it_again
origin: src/kraft/remote/local_jobs.py §recover

## REQ server-restart-asks-the-worker-which-sessions-it-holds
WHEN the server restarts under a running local session, the system SHALL adopt the session if the worker holds it, and SHALL NOT adopt one the worker does not hold.
enforced-by: tests/worker/test_reattach_local.py::test_live_local_session_is_adopted_when_the_worker_holds_it, tests/worker/test_reattach_local.py::test_a_live_process_the_worker_does_not_hold_is_not_adopted, tests/worker/test_reattach_local.py::test_a_live_session_started_before_the_worker_existed_is_adopted
origin: src/kraft/worker/reattach.py §_still_there

## REQ session-the-worker-does-not-hold-ends-by-evidence
WHEN a local session is not held by the worker, the system SHALL end it from its result file or exit file, and `unknown` when there is none, and SHALL NOT end it `failed` for want of evidence.
enforced-by: tests/worker/test_reattach_local.py::test_local_row_the_worker_does_not_hold_ends_by_evidence[result-file-present], tests/worker/test_reattach_local.py::test_local_row_the_worker_does_not_hold_ends_by_evidence[exit-file-present], tests/worker/test_reattach_local.py::test_local_row_the_worker_does_not_hold_ends_by_evidence[no-evidence]
origin: src/kraft/worker/reattach.py

## REQ cancel-after-the-launch-frame-stops-the-session
WHEN a launch is cancelled or the connection drops after the worker was asked to start a session, the system SHALL have the worker stop whatever it started.
enforced-by: tests/remote/test_local_link.py::test_cancel_after_the_spawn_frame_stops_what_the_worker_started, tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting[resent-once-the-stop-is-answered], tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting[gives-up-after-three], tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting[stop-unanswered]
origin: src/kraft/remote/local_link.py §spawn

## REQ a-dropped-spawn-is-sent-again-once-its-stop-is-answered
WHEN the connection drops while a session is being started and a worker then answers the stop for it, the system SHALL send the start again, a bounded number of times, and SHALL NOT let the worker run two sessions for one key.
enforced-by: tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting[resent-once-the-stop-is-answered], tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting[gives-up-after-three], tests/remote/test_local_jobs.py::test_a_key_that_is_running_is_not_started_twice
origin: src/kraft/remote/local_link.py §spawn

## REQ local-step-waits-for-the-worker
WHILE the local worker is not connected, the system SHALL park an ordinary local step as waiting with no session row, and SHALL NOT run it in the server.
enforced-by: tests/executor/test_dispatch_remote.py::test_local_step_parks_while_the_worker_is_down[still-down]
origin: src/kraft/executor/dispatch.py §_await_local_worker

## REQ local-launch-without-placement-waits-for-the-worker
WHILE the local worker is not connected, a local launch that skips placement SHALL wait for it, say `waiting for local` in the log and once on the board, and not count the wait against its time cap.
enforced-by: tests/adapters/test_subprocess_local.py::test_escalation_launch_waits_for_the_worker
origin: src/kraft/adapters/subprocess.py §_start_locally

## REQ server-starts-a-worker-and-keeps-the-link
WHEN the server starts with no link to a local worker, it SHALL start one and wait for it before resuming walks, and WHEN the boot fails after that, it SHALL stop trying to start workers.
enforced-by: tests/api/test_startup.py::test_startup_local_link_none_installed_waits_for_the_worker_before_resuming, tests/api/test_startup.py::test_startup_local_link_boot_fails_after_the_link
origin: src/kraft/api/startup.py

## REQ settings-lists-the-local-worker-apart
The system SHALL list the local worker first among the workers, with its running sessions and no capacity, and SHALL NOT let it be removed.
enforced-by: tests/api/test_workers.py::test_the_local_worker_is_listed_first_and_cannot_be_removed, tests/api/test_workers.py::test_the_local_workers_running_count_comes_from_the_session_rows, tests/cli/test_worker.py::test_list_with_no_machines_shows_only_the_local_worker
origin: src/kraft/api/routes/workers.py

## REQ clean-stop-leaves-a-running-local-session-running
WHEN the server stops cleanly, the system SHALL leave a local session of a worktree item that is still running, uncapped and not paused running, with its row `running`, its sandbox and its egress route open, for the next start to adopt through the worker.
enforced-by: tests/adapters/test_subprocess_local.py::test_clean_stop_keep_rule[kept], tests/adapters/test_subprocess_local.py::test_a_kept_sandboxed_session_keeps_its_container[sandboxed], tests/worker/test_reattach_local.py::test_adopted_sandboxed_session_survives_a_second_stop[kept-by-a-clean-stop], tests/adapters/test_subprocess_in_place.py::test_a_running_session_cancelled_by_a_stopping_server[worktree-is-kept]
origin: src/kraft/adapters/subprocess.py §kept_through_stop

## REQ clean-stop-still-stops-what-it-must
WHEN the server stops cleanly, the system SHALL stop a local session that a person paused, that is over its cap or has exited, that belongs to an item running in place, or that runs on another machine, as it did before the worker.
enforced-by: tests/adapters/test_subprocess_local.py::test_clean_stop_keep_rule[paused-by-a-person], tests/adapters/test_subprocess_local.py::test_a_session_over_its_cap_is_capped_out_while_the_server_stops, tests/adapters/test_subprocess_local.py::test_a_session_that_exits_while_the_server_stops_is_closed_sandboxed, tests/adapters/test_subprocess_in_place.py::test_a_running_session_cancelled_by_a_stopping_server[in-place-is-stopped-while-stopping], tests/adapters/test_subprocess_remote.py::test_a_remote_standin_is_stopped_by_a_clean_stop
origin: src/kraft/adapters/subprocess.py §kept_through_stop

## REQ stop-now-ends-the-local-sessions
WHEN `kraft admin stop --now` stops the server, the system SHALL write the stop-now marker before the SIGTERM, the stopping server SHALL read and delete it and stop every local session, and a marker no server read SHALL NOT turn a later clean stop into one.
enforced-by: tests/cli/test_admin_running_agents.py::test_stop_now_marks_the_stop_for_the_server_and_says_nothing_is_kept, tests/cli/test_admin_running_agents.py::test_stop_now_writes_the_marker_only_when_a_server_runs, tests/adapters/test_subprocess_local.py::test_clean_stop_keep_rule[now-stops-it], tests/api/test_startup.py::test_a_stop_now_marker_ends_the_local_sessions_a_clean_stop_keeps[stop-now-ends], tests/api/test_startup.py::test_a_stop_now_marker_ends_the_local_sessions_a_clean_stop_keeps[clean-stop-keeps], tests/api/test_startup.py::test_boot_deletes_a_stop_now_marker_no_server_read
origin: src/kraft/api/startup.py

## REQ stop-says-how-many-steps-keep-running
WHEN `kraft admin stop` stops a server running local steps of worktree items, it SHALL say how many keep running and that nothing answers their permission prompts or enforces their caps until Kraft is back.
enforced-by: tests/cli/test_admin_running_agents.py::test_stop_says_local_steps_keep_running[two], tests/cli/test_admin_running_agents.py::test_stop_says_local_steps_keep_running[in-place-one-of-two]
origin: src/kraft/cli/admin.py §_confirm_running_agents

## REQ each-installed-kraft-has-its-own-worker
The system SHALL run the local worker of each installed kraft on a socket, lock and pid file named for that install, so that a worker of another install is neither displaced nor given new sessions.
enforced-by: tests/remote/test_local_serve.py::test_install_id_differs_for_a_reinstall_of_the_same_version, tests/remote/test_local_serve.py::test_workers_of_two_installs_run_side_by_side, tests/remote/test_local_link.py::test_start_worker_goes_out_of_the_units_cgroup_under_systemd[detached]
origin: src/kraft/remote/local_serve.py §install_id

## REQ new-worker-adopts-the-old-workers-sessions
WHEN a server of a newer install starts its worker while the older one holds sessions, the new worker SHALL hold them by pid identity, and the older one SHALL exit once it has reaped its own, leaving no record behind.
enforced-by: tests/remote/test_local_worker_process.py::test_new_install_worker_adopts_the_old_workers_sessions, tests/remote/test_local_serve.py::test_two_workers_reaping_one_record_do_not_fail
origin: src/kraft/remote/local_jobs.py

## REQ service-worker-starts-outside-the-units-cgroup
WHILE Kraft runs as a systemd service, the system SHALL start the local worker in a scope of its own outside the unit's cgroup, and SHALL start it the plain way when `systemd-run` fails.
enforced-by: tests/remote/test_local_link.py::test_start_worker_goes_out_of_the_units_cgroup_under_systemd[service-linux], tests/remote/test_local_link.py::test_start_worker_goes_out_of_the_units_cgroup_under_systemd[systemd-run-fails]
origin: src/kraft/remote/local_link.py §_start_worker

## REQ new-major-update-waits-for-running-steps
WHEN `kraft admin update --restart` would move a running server to a new major version, the system SHALL mark the release for when Kraft is free instead of restarting, unless `--now` is given, and SHALL refuse `kraft admin restart` onto a new major while an item is active.
enforced-by: tests/cli/test_admin_running_agents.py::test_update_across_a_major_waits_for_running_steps[across-a-major-waits-for-free], tests/cli/test_admin_running_agents.py::test_update_across_a_major_waits_for_running_steps[now-restarts-at-once], tests/cli/test_admin_running_agents.py::test_restart_onto_a_new_major_with_an_item_active_is_refused[item-active], tests/cli/test_admin_running_agents.py::test_crosses_major[1.4-to-2.0]
origin: src/kraft/cli/admin.py §_crosses_major

## REQ uninstall-service-says-a-worker-remains
WHEN `kraft admin uninstall-service` removes the unit while this home's local worker answers, it SHALL say the worker is still finishing its steps and exits on its own.
enforced-by: tests/cli/test_admin_running_agents.py::test_uninstall_service_says_a_worker_is_still_finishing_its_steps[worker-running]
origin: src/kraft/cli/admin.py §_say_worker_outlives
