# Remote workers

The other machines Kraft may run steps on, and the connections it keeps to
them: how a machine is registered, how it proves who it is, and what Kraft
tells a person when one is not connected. In this release a connected machine
runs nothing yet.

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
