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
enforced-by: tests/remote/test_local_link.py::test_cancel_after_the_spawn_frame_stops_what_the_worker_started, tests/remote/test_local_link.py::test_a_dropped_answer_stops_the_session_after_reconnecting
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
