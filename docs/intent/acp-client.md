# ACP client

What Kraft's client does between the session and an ACP agent (local, unsandboxed).

## REQ acp-launch-is-one-session-and-one-prompt
WHEN an ACP launch runs, the client SHALL open one session and send one `session/prompt`, then close the agent's stdin and end it, and SHALL exit 0 after a completed turn.
enforced-by: tests/acp/test_client.py::test_one_launch_is_one_session_one_prompt_and_exit_zero
origin: src/kraft/acp/client.py

## REQ acp-client-ends-an-agent-that-will-not-exit
WHEN the turn is over, the client SHALL close the agent's stdin, wait the grace, then terminate and finally kill it, so an agent that ignores EOF (or SIGTERM) is gone within the grace plus two seconds and a start-up margin.
enforced-by: tests/acp/test_client.py::test_an_agent_that_will_not_exit_is_ended_within_the_grace_and_the_ladder[ignores-stdin-eof-sigterm-ends-it], tests/acp/test_client.py::test_an_agent_that_will_not_exit_is_ended_within_the_grace_and_the_ladder[ignores-sigterm-too-sigkill-ends-it]
origin: src/kraft/acp/client.py

## REQ acp-client-never-waits-on-a-dead-agent
IF the agent's output ends, or the client's own reader fails, THEN every waiting request SHALL fail at once with the agent gone, and a request made afterwards SHALL fail at once too, never hang.
enforced-by: tests/acp/test_client.py::test_an_agent_that_dies_before_session_new_ends_the_launch_not_hangs_it, tests/acp/test_client.py::test_request_after_the_agent_is_gone_raises_at_once, tests/acp/test_client.py::test_a_pump_that_raises_is_logged_and_treated_as_eof_not_silent
origin: src/kraft/acp/client.py

## REQ acp-client-bounds-the-handshake
IF the agent does not answer `initialize`, `session/new`, `session/resume`, `session/load` or `session/set_config_option` within the launch's handshake timeout, THEN the client SHALL fail the launch naming the request, and SHALL drop a late answer to any request it has stopped waiting for.
enforced-by: tests/acp/test_client.py::test_an_agent_that_never_answers_initialize_fails_with_a_reason_not_a_hang, tests/acp/test_client.py::test_a_late_answer_after_the_cancel_wait_is_dropped_not_a_reader_failure
origin: src/kraft/acp/client.py

## REQ acp-client-logs-and-survives-what-an-agent-should-not-send
WHEN an agent prints a line that is not JSON-RPC or longer than the line limit, on stdout or on stderr, the client SHALL log it (`stray`, `line_too_long`) and still finish the turn, SHALL keep draining stderr past what a pipe holds, and SHALL answer a request it cannot read (an id that is not a string or number, params that are not an object) with an invalid-request error.
enforced-by: tests/acp/test_client.py::test_a_non_json_stdout_line_is_logged_as_stray_and_the_turn_still_ends, tests/acp/test_client.py::test_a_line_past_the_limit_is_skipped_logged_and_the_turn_still_ends, tests/acp/test_client.py::test_stderr_is_drained_past_what_a_pipe_holds_and_past_a_line_over_the_limit, tests/acp/test_client.py::test_an_agent_request_with_an_unhashable_id_or_non_object_params_is_answered_invalid
origin: src/kraft/acp/client.py

## REQ acp-client-exits-with-the-agents-code-when-it-died-early
IF the agent exits before answering the prompt, THEN the client SHALL exit with the agent's own exit code, and 128 plus the signal number for a signal.
enforced-by: tests/acp/test_client.py::test_the_agents_own_exit_code_is_the_clients_when_it_died_before_the_turn_ended[agent-died-mid-turn], tests/acp/test_client.py::test_the_agents_own_exit_code_is_the_clients_when_it_died_before_the_turn_ended[agent-sigkilled-mid-turn-reads-as-a-shell-would]
origin: src/kraft/acp/client.py

## REQ acp-client-refuses-host-access-requests
IF an ACP agent sends the client any request other than `session/request_permission`, THEN the client SHALL answer it with a method-not-found error.
enforced-by: tests/acp/test_client.py::test_an_fs_request_gets_method_not_found_and_initialize_offers_no_fs
origin: src/kraft/acp/client.py

## REQ acp-client-failures-are-the-result-file
IF the client itself fails (agent not startable, handshake or login refused), THEN it SHALL write a `failed` result file unless the agent wrote one, and SHALL NOT overwrite the agent's.
enforced-by: tests/acp/test_client.py::test_an_agent_that_cannot_start_is_a_config_error_with_a_failed_result_file[missing-binary], tests/acp/test_client.py::test_an_agent_that_cannot_start_is_a_config_error_with_a_failed_result_file[empty-argv], tests/acp/test_client.py::test_the_agents_own_result_file_is_never_overwritten
origin: src/kraft/acp/client.py

## REQ acp-client-answers-asks-allow-once-until-part-2
WHILE no tool policy can be held over ACP, the client SHALL answer each permission ask with the option whose kind is `allow_once`, never an `*_always` option, and log the ask.
enforced-by: tests/acp/test_client.py::test_a_permission_ask_is_answered_allow_once_by_kind_and_logged
origin: src/kraft/acp/client.py

## REQ acp-resume-prefers-session-resume-then-session-load
WHEN an ACP launch resumes, the client SHALL use `session/resume` when the agent offers it, else `session/load` with the replayed history marked, else fail the launch.
enforced-by: tests/acp/test_client.py::test_a_resume_prefers_session_resume_then_session_load[resume-preferred], tests/acp/test_client.py::test_a_resume_prefers_session_resume_then_session_load[load-fallback], tests/acp/test_client.py::test_an_agent_that_cannot_resume_at_all_fails_the_launch
origin: src/kraft/acp/client.py

## REQ acp-context-is-a-system-prompt-object-when-offered
WHERE the agent advertises `sessionCapabilities.systemPrompt`, the client SHALL send Kraft's context as an object `{mode: append, content: [...]}` on a new or resumed session, and otherwise SHALL fold it into the prompt.
enforced-by: tests/acp/test_client.py::test_context_rides_the_system_prompt_object_or_folds_into_the_prompt[object-when-offered], tests/acp/test_client.py::test_context_rides_the_system_prompt_object_or_folds_into_the_prompt[folded-when-not], tests/acp/test_client.py::test_context_rides_the_system_prompt_object_or_folds_into_the_prompt[resume-object-when-offered], tests/acp/test_client.py::test_context_rides_the_system_prompt_object_or_folds_into_the_prompt[resume-folded]
origin: src/kraft/acp/client.py

## REQ acp-refused-config-option-is-a-config-error
IF the agent refuses a model or effort value, THEN the launch SHALL end as `config_error` naming the option and the values the agent offered.
enforced-by: tests/acp/test_client.py::test_a_refused_config_option_is_a_config_error_naming_what_was_offered
origin: src/kraft/acp/client.py

## REQ acp-pause-cancels-the-turn-and-keeps-its-usage
WHEN the client receives SIGINT during a turn, it SHALL send `session/cancel`, wait for the prompt response for less than Kraft's flush grace so its usage is logged before the group is killed, and exit 130; WHERE the agent also received the pause's group SIGINT and exited first, with any exit code, the client SHALL still exit 130 and write no failed result; WHEN SIGINT arrives before the prompt is sent, the client SHALL send none and exit 130.
enforced-by: tests/acp/test_client.py::test_sigint_becomes_session_cancel_and_the_usage_still_lands, tests/acp/test_client.py::test_the_cancel_wait_ends_before_kraft_kills_the_group_after_a_pause, tests/acp/test_client.py::test_a_group_sigint_ends_the_client_130_whatever_the_agent_exits_with[agent-dies-on-it], tests/acp/test_client.py::test_a_group_sigint_ends_the_client_130_whatever_the_agent_exits_with[agent-exits-0-on-it], tests/acp/test_client.py::test_an_agent_that_is_gone_after_a_signal_is_that_signals_code_not_a_failure[exit-0-after-sigint], tests/acp/test_client.py::test_an_agent_that_is_gone_after_a_signal_is_that_signals_code_not_a_failure[exit-1-after-sigint], tests/acp/test_client.py::test_a_sigint_during_the_handshake_starts_no_turn
origin: src/kraft/acp/client.py

## REQ acp-sigterm-is-forwarded-to-the-agent
WHEN the client receives SIGTERM, it SHALL forward it to the agent and exit with the agent's code, or 143 when the agent exits 0 after it.
enforced-by: tests/acp/test_client.py::test_sigterm_is_forwarded_to_the_agent_and_its_code_is_the_clients, tests/acp/test_client.py::test_an_agent_that_is_gone_after_a_signal_is_that_signals_code_not_a_failure[exit-0-after-sigterm], tests/acp/test_client.py::test_an_agent_that_is_gone_after_a_signal_is_that_signals_code_not_a_failure[term-wins]
origin: src/kraft/acp/client.py

## REQ acp-client-config-failure-resolves-config-error
WHEN the client logs a `config_error` and the session would otherwise resolve `failed`, the system SHALL resolve it `config_error`, so no fix loop relaunches into the same refusal; a session adopted after a restart, and an agent that dies before any session exists, resolve `failed`.
enforced-by: tests/adapters/test_agent_acp_status.py::test_a_logged_config_error_is_the_sessions_status[config-error-event], tests/adapters/test_agent_acp_status.py::test_a_logged_config_error_is_the_sessions_status[plain-error]
origin: src/kraft/adapters/agent.py

## REQ acp-opencode-contract
The system SHALL treat `opencode acp`'s answer to one turn as a `stopReason` plus a per-turn usage block whose parts sum to its `totalTokens`.
enforced-by: tests/acp/test_client_e2e.py::test_opencode_acp_answers_one_turn_with_usage
origin: src/kraft/acp/client.py
