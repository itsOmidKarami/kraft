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

## REQ acp-no-policy-asks-are-allowed-locally
WHERE a launch holds no tool policy, the client SHALL answer each permission ask with the option whose kind is `allow_once`, without asking the gate, and log it.
enforced-by: tests/acp/test_client.py::test_a_permission_ask_is_answered_allow_once_by_kind_and_logged, tests/acp/test_client_policy.py::test_without_a_tool_policy_an_ask_is_allowed_locally_and_never_reaches_the_gate
origin: src/kraft/acp/client.py

## REQ acp-asks-are-decided-by-the-gate-on-kraft-tool-names
WHEN the agent asks permission and the launch holds a tool policy, the client SHALL name the tool from the call's `name`, else its `kind`, through the entry's `tool_names`, and ask the permission gate as this session.
enforced-by: tests/acp/test_tools.py::test_a_tool_call_is_named_by_its_name_else_its_kind[named], tests/acp/test_tools.py::test_a_tool_call_is_named_by_its_name_else_its_kind[kind-only], tests/acp/test_client_policy.py::test_an_ask_is_decided_by_the_gate_on_krafts_tool_names[named-and-allowed], tests/acp/test_client_policy.py::test_an_ask_is_decided_by_the_gate_on_krafts_tool_names[named-and-denied], tests/acp/test_client_policy.py::test_an_ask_is_decided_by_the_gate_on_krafts_tool_names[keyed-on-kind]
origin: src/kraft/acp/tools.py

## REQ acp-asks-are-answered-allow-once-or-reject-once
The client SHALL answer a permission ask only with a `allow_once` or `reject_once` option, never an `*_always` option.
enforced-by: tests/acp/test_client_policy.py::test_an_allowing_ask_with_only_an_always_option_is_answered_cancelled_never_always, tests/acp/test_client_policy.py::test_an_ask_is_decided_by_the_gate_on_krafts_tool_names[named-and-denied]
origin: src/kraft/acp/client.py

## REQ acp-policy-fails-closed
IF a launch holds a tool policy and the permission gate cannot be reached or cannot read the task's policy, THEN the client SHALL reject the ask.
enforced-by: tests/acp/test_client_policy.py::test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name[gate-unreachable], tests/acp/test_client_policy.py::test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name[gate-cannot-read-the-policy], tests/acp/test_client_policy.py::test_an_ask_is_decided_by_the_gate_on_krafts_tool_names[named-and-allowed]
origin: src/kraft/acp/client.py

## REQ acp-allowlist-rejects-what-it-cannot-name
IF a launch holds an allowlist and an ask is for a call with no `name` whose `kind` is `other` or maps to no Kraft tool, THEN the client SHALL reject it.
enforced-by: tests/acp/test_client_policy.py::test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name[allowlist-unnameable-kind-other], tests/acp/test_client_policy.py::test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name[allowlist-unmapped-kind]
origin: src/kraft/acp/client.py

## REQ acp-always-deny-is-rejected-whatever-the-policy
The client SHALL reject an ask for a tool in the entry's `always_deny` without asking the gate.
enforced-by: tests/acp/test_client_policy.py::test_an_always_denied_tool_never_reaches_the_gate, tests/acp/test_client_policy.py::test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name[always-deny-wins-over-an-allowing-gate]
origin: src/kraft/acp/client.py

## REQ acp-asks-everything-is-applied-on-every-launch
WHEN an ACP launch starts, new or resumed, with or without a tool policy, the client SHALL apply the entry's `asks_everything`: its env to the agent's environment only, its config options and mode to the session.
enforced-by: tests/acp/test_client_policy.py::test_asks_everything_makes_the_agent_ask_on_every_launch[env], tests/acp/test_client_policy.py::test_asks_everything_makes_the_agent_ask_on_every_launch[config], tests/acp/test_client_policy.py::test_asks_everything_makes_the_agent_ask_on_every_launch[mode-as-set-mode], tests/acp/test_client_policy.py::test_asks_everything_makes_the_agent_ask_on_every_launch[applied-on-a-resumed-launch-too], tests/acp/test_client_policy.py::test_the_same_launch_without_asks_everything_makes_no_ask
origin: src/kraft/acp/client.py

## REQ acp-refused-asking-mode-is-a-config-error
IF the agent refuses the entry's `asks_everything` mode or config option, THEN the launch SHALL end as `config_error` naming it.
enforced-by: tests/acp/test_client_policy.py::test_an_agent_that_refuses_the_asking_mode_ends_the_launch_as_config_error
origin: src/kraft/acp/client.py

## REQ acp-withdrawn-ask-is-answered-cancelled
WHEN the agent withdraws an ask with `$/cancel_request` before the client has answered it, the client SHALL answer the ask with the `cancelled` outcome without waiting for the gate.
enforced-by: tests/acp/test_client_policy.py::test_a_withdrawn_ask_is_answered_cancelled_while_the_gate_is_still_thinking
origin: src/kraft/acp/client.py

## REQ acp-asks-do-not-block-the-reader
WHILE an ask waits on the gate, the client SHALL keep reading the agent's messages.
enforced-by: tests/acp/test_client_policy.py::test_two_asks_in_flight_are_both_answered, tests/acp/test_client_policy.py::test_a_withdrawn_ask_is_answered_cancelled_while_the_gate_is_still_thinking
origin: src/kraft/acp/client.py

## REQ acp-log-is-not-silent-while-an-ask-waits
WHILE an ask waits on the gate, the client SHALL write a `heartbeat` log line every `heartbeat_s` seconds.
enforced-by: tests/acp/test_client_policy.py::test_the_log_is_not_silent_while_an_ask_waits_on_the_gate
origin: src/kraft/acp/client.py

## REQ acp-backstop-ends-a-session-that-ran-what-policy-forbids
IF a launch holds a tool policy and a tool call completes with no allowing ask on record and the policy does not allow it, THEN the client SHALL cancel the turn, log `policy_violation` and exit 77, and SHALL NOT overwrite a result file the agent wrote.
enforced-by: tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-and-denied], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-and-unnameable-under-an-allowlist], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[ran-after-a-rejection], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[withdrawn-ask-that-runs-anyway], tests/acp/test_client_policy.py::test_a_violation_with_no_result_writes_a_failed_one_naming_it[absent]
origin: src/kraft/acp/client.py

## REQ acp-backstop-ignores-what-ran-with-permission
The client SHALL NOT report a violation for a call the policy allows, a call the entry's `settles_itself` names, a call that was asked and allowed, an in-progress report that precedes its ask, or an unasked call that ends `failed` after Kraft rejected a tool in the same turn (the agent cancelled it with the turn), and SHALL NOT run the backstop for a launch with no tool policy.
enforced-by: tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-but-allowed], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-but-settled-by-the-agent], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[asked-and-allowed-after-an-early-in_progress], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[asked-rejected-and-failed], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-and-cancelled-by-a-rejection-that-ended-the-turn], tests/acp/test_client_policy.py::test_a_launch_without_a_policy_has_no_backstop, tests/acp/test_client_policy.py::test_a_rejected_default_call_ends_failed_not_as_a_violation
origin: src/kraft/acp/client.py

## REQ acp-policy-violation-is-never-done
IF an ACP session's log holds a `policy_violation`, THEN the system SHALL resolve the session `config_error`, whatever result the agent wrote.
enforced-by: tests/adapters/test_agent_acp_status.py::test_a_logged_config_error_is_the_sessions_status[policy-violation-beats-done], tests/adapters/test_agent_acp_status.py::test_a_logged_config_error_is_the_sessions_status[policy-violation-only-for-acp], tests/acp/test_log.py::test_policy_violation_names_the_first_one
origin: src/kraft/adapters/agent.py

## REQ acp-stop-reason-names-the-cause
IF an ACP session stops for a config error, THEN the system SHALL give as the stop's cause the message of the log's `config_error` event, never the client's `starting` record.
enforced-by: tests/executor/test_stops.py::test_an_acp_stop_names_its_cause_not_the_clients_first_line[acp-config-error-not-the-starting-line], tests/acp/test_log.py::test_cause_of_an_acp_log[config-error-beats-violation], tests/acp/test_log.py::test_cause_of_an_acp_log[not-an-acp-log-is-none]
origin: src/kraft/executor/walk.py

## REQ acp-policy-violation-stop-says-so
IF an ACP session ran and its log holds a `policy_violation`, THEN the system SHALL stop the item with a reason that says the tool policy was violated, not that the task could not start.
enforced-by: tests/executor/test_stops.py::test_an_acp_stop_names_its_cause_not_the_clients_first_line[acp-policy-violation-says-so]
origin: src/kraft/executor/walk.py

## REQ acp-opencode-asks-per-call-and-a-rejection-holds
The system SHALL treat `opencode acp` with `{"permission":{"*":"ask"}}` as asking before each tool call and as honouring a rejected ask.
enforced-by: tests/acp/test_client_e2e.py::test_opencode_asks_per_call_and_a_rejection_holds
origin: src/kraft/acp/client.py

## REQ acp-opencode-unasked-call-is-caught
IF `opencode acp` runs a call it was not asked about under a tool policy, THEN the client SHALL end the session as a policy violation.
enforced-by: tests/acp/test_client_e2e.py::test_the_backstop_catches_an_opencode_that_does_not_ask
origin: src/kraft/acp/client.py

## REQ acp-a-rejection-that-ends-the-turn-is-continued
WHEN the agent ends a turn `cancelled` directly after the client answered `reject_once`, and neither a pause, a stop nor a violation caused it, the client SHALL send another `session/prompt` on the same session that names the rejected tool and the gate's reason and says the tool is not available in this task and that other calls that failed as declined when the turn ended were cancelled with it, not denied, and can be tried again; it SHALL do so at most `max_continuations` times per launch, and a pause SHALL still end the launch with 130.
enforced-by: tests/acp/test_client_policy.py::test_a_rejection_that_ends_the_turn_is_continued_on_the_same_session_until_done, tests/acp/test_client_policy.py::test_a_pause_during_a_continuation_still_ends_130, tests/acp/test_client_policy.py::test_a_pause_that_lands_as_a_rejected_turn_ends_is_not_continued_past
origin: src/kraft/acp/client.py

## REQ acp-continuation-cap-is-a-stop-naming-the-tool-and-limit
IF the agent ends its turn after a rejection once the continuations are spent, THEN the client SHALL log `rejection_limit` naming the tool, the limit and the count, write a failed result, and exit 77, and the system SHALL resolve the session `config_error` and stop the item with that message, saying the session stopped rather than failed to start.
enforced-by: tests/acp/test_client_policy.py::test_continuations_are_capped_and_the_stop_names_the_tool_and_the_limit, tests/acp/test_log.py::test_cause_of_an_acp_log[rejection-limit-has-its-own-prefix], tests/adapters/test_agent_acp_status.py::test_a_logged_config_error_is_the_sessions_status[rejection-limit], tests/executor/test_stops.py::test_an_acp_stop_names_its_cause_not_the_clients_first_line[acp-rejection-limit-says-so]
origin: src/kraft/acp/client.py

## REQ acp-failed-result-replaces-an-empty-file
WHEN the client writes a failed result and the result file is absent or empty, it SHALL write it; a result file that holds anything is never overwritten.
enforced-by: tests/acp/test_client_policy.py::test_a_violation_with_no_result_writes_a_failed_one_naming_it[pre-created-empty], tests/acp/test_client.py::test_a_login_the_agent_wants_is_a_failed_result_not_a_hang[pre-created-empty], tests/acp/test_client.py::test_the_agents_own_result_file_is_never_overwritten
origin: src/kraft/acp/client.py

## REQ acp-backstop-fails-closed-and-keeps-the-record
The backstop SHALL treat a call that ended `failed` with no ask on record as it treats a completed one, SHALL decide a call again when a late `name` makes it another Kraft tool than the one an ask allowed, SHALL end the session as a violation when the check itself raises, and SHALL log a violation that arrives together with a pause or stop while keeping that exit code.
enforced-by: tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[unasked-and-failed-after-running], tests/acp/test_client_policy.py::test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow[a-late-name-makes-an-allowed-call-another-tool], tests/acp/test_client_policy.py::test_a_backstop_that_raises_is_a_violation_not_silence, tests/acp/test_client_policy.py::test_a_violation_that_meets_a_pause_keeps_the_pauses_code_and_is_still_logged
origin: src/kraft/acp/client.py
