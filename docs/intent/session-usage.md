# Session usage, cost and budget

What a `worker_sessions` row is allowed to say about tokens and cost while an
agent is still running, versus once it has exited, and what a budget cap sees
of a session that has not exited yet.

## REQ live-output-tokens-are-unknown-not-wrong
WHILE a claude session is running, the system SHALL report `tokens_out` as unknown (`NULL`) rather than the partial, start-of-message count a stream-json `assistant` line carries, and SHALL fill it in only from the session's exit envelope.
enforced-by: tests/test_usage.py::test_stream_usage_sums_distinct_requests, tests/test_usage.py::test_stream_usage_keeps_cache_tokens_apart, tests/adapters/test_subprocess.py::test_running_session_row_carries_tokens_before_exit[clean], tests/worker/test_reattach.py::test_an_adopted_session_carries_live_tokens_before_it_exits[clean]
origin: src/kraft/usage.py §from_stream (Kraft-wz83s)

## REQ a-running-sessions-cost-is-estimated-from-its-tokens
WHILE a session is running, the system SHALL price its known live tokens (input, cache read, cache write) against a packaged per-model rate table and store the result as `cost_usd`, marking the row `cost_estimated`, unless the session's model is not in the table, in which case `cost_usd` SHALL stay `NULL`.
enforced-by: tests/store/test_sessions.py::test_session_progress_estimates_cost_for_a_known_model, tests/store/test_sessions.py::test_session_progress_estimates_nothing_for_an_unknown_model, tests/test_usage_pricing.py::test_estimate_cost_prices_live_tokens_for_a_known_model, tests/test_usage_pricing.py::test_estimate_cost_is_none_for_an_unpriced_model
origin: src/kraft/usage.py §estimate_cost, src/kraft/store/sessions.py §session_progress (Kraft-wz83s)

## REQ cache-write-is-priced-at-the-one-hour-tier
The system SHALL price a claude session's cache-write tokens at Anthropic's 1-hour cache-creation rate (2x the model's input rate), not models.dev's own 5-minute figure, so `estimate_cost` reproduces a real session's own reported `total_cost_usd`.
enforced-by: tests/test_usage_pricing.py::test_estimate_cost_reproduces_a_real_sessions_bill
origin: dev/refresh_prices.py §CACHE_WRITE_MULTIPLE (Kraft-wz83s)

## REQ exit-replaces-the-estimate-and-never-leaves-it-standing
WHEN a session exits or is paused and the read carries a reported cost, the system SHALL overwrite `cost_usd` with that figure and SHALL clear `cost_estimated`. IF the read carries no reported cost, THEN the system SHALL keep or refresh `cost_usd` as an estimate from the session's final tokens (output included) rather than discard it to `NULL`, and SHALL leave `cost_estimated` set, unless the session's model is not in the rate table, or the session resumed an earlier CLI session whose own totals could not be netted against (`a-resumed-cli-session-is-counted-once`), in which case `cost_usd` SHALL stay `NULL`.
enforced-by: tests/store/test_sessions.py::test_exit_replaces_the_estimate_with_the_real_cost_and_clears_the_flag, tests/store/test_sessions.py::test_exit_with_no_reported_cost_keeps_an_estimate_from_final_tokens, tests/store/test_sessions.py::test_exit_with_no_reported_cost_and_no_priced_model_stays_null, tests/store/test_sessions.py::test_pause_also_clears_a_running_estimate, tests/store/test_sessions.py::test_pause_with_no_reported_cost_keeps_an_estimate_from_final_tokens, tests/test_usage_pricing.py::test_a_finished_priced_session_with_no_reported_cost_is_complete_but_estimated, tests/store/test_resumed_usage.py::test_a_paused_estimate_is_not_doubled_by_a_resumed_reset
origin: src/kraft/store/sessions.py §session_exited, §record_pause_usage, §_settle_cost, §_own_share (Kraft-wz83s)

## REQ budget-counts-a-running-sessions-estimated-spend
WHILE a work item has a running session with an estimated cost, the system SHALL include that estimate in `budget_spend` and in `caps.budget_breach`, so a dollar cap can stop a launch before the session that would breach it has exited.
enforced-by: tests/store/test_budget.py::test_budget_spend_includes_a_running_sessions_estimate, tests/executor/test_budget_caps.py::test_a_running_sessions_estimate_trips_a_usd_cap
origin: src/kraft/store/budget.py §budget_spend, src/kraft/caps.py §budget_breach (Kraft-wz83s)

## REQ a-session-is-priced-on-its-launch-model
WHEN an agent session's output names no model, or only a display name ("Auto"), the system SHALL price it, and record it, on the model Kraft launched it with, and SHALL NOT replace a model the output did name.
enforced-by: tests/store/test_sessions.py::test_a_session_is_priced_on_its_launch_model_when_its_output_names_none[no-model], tests/store/test_sessions.py::test_a_session_is_priced_on_its_launch_model_when_its_output_names_none[display-name], tests/store/test_sessions.py::test_a_session_is_priced_on_its_launch_model_when_its_output_names_none[reported], tests/adapters/test_agent_launch_model.py::test_an_agent_launch_hands_its_model_to_the_session, tests/adapters/test_agent_launch_model.py::test_run_task_records_the_launch_model_on_the_row, tests/executor/test_budget_caps.py::test_a_codex_session_counts_toward_the_item_cap_at_its_launch_models_estimate
origin: src/kraft/store/sessions.py §_with_launch_model (Kraft-9efnk.10, option A2)

## REQ unpriced-spend-warns-and-never-stops-the-instance-caps
IF a finished session of a `kind: cli` harness spent tokens with no reported cost and no `prices.json` rate for its model, THEN `budget.work_item_usd` and `budget.daily_usd` SHALL count it as $0 and SHALL NOT stop for it, the system SHALL append one `spend_unpriced` event per work item saying so, and `kraft admin doctor` SHALL warn for each no-cost harness the chains launch on an unpriced model or none. A per-scope `budget_usd` keeps refusing on it (`scope-budgets-cap-their-own-spend`).
enforced-by: tests/executor/test_budget_caps.py::test_unpriced_spend_warns_once_and_never_stops_the_item_or_daily_cap, tests/test_agent_profiles.py::test_doctor_warns_per_no_cost_harness_launched_on_an_unpriced_model
origin: src/kraft/store/sessions.py §_warn_unpriced, src/kraft/doctor.py §_cost_checks (Kraft-9efnk.10, option A2)

## REQ rollups-expose-that-a-total-includes-an-estimate
WHEN any session folded into a usage rollup is still running on an estimated cost, the system SHALL mark that rollup's total `cost_estimated`, distinct from `cost_complete`, so a reader can render the figure as an estimate rather than a settled total.
enforced-by: tests/test_usage.py::test_rollup_marks_the_total_estimated_when_a_running_session_has_a_guess
origin: src/kraft/store/budget.py §usage_rollup (Kraft-wz83s)

## REQ subagent-tracking-leaves-usage-unchanged
WHEN a session's log carries sub-agent lines, the system SHALL read its usage and cost exactly as it reads them without sub-agent tracking.
enforced-by: tests/test_usage.py::test_a_log_with_several_result_envelopes_counts_every_invocation[sub-agent-progress-lines]
origin: src/kraft/usage.py §read_envelope

## REQ acp-usage-is-per-turn-and-context-size-is-not-spend
The system SHALL read an ACP session's tokens from each prompt response's usage block (output plus thought tokens, so the parts sum to the agent's total) and its cost from the last `usage_update` cost of each agent session (so a resume of one session is not counted twice, and a fresh session on a later launch adds its own), SHALL count the turns of every launch a log holds, SHALL keep a cost that arrives with no prompt usage, SHALL skip history replayed by `session/load`, and SHALL NOT count `usage_update.used` as tokens or cost.
enforced-by: tests/acp/test_reader.py::test_a_turns_tokens_and_the_cumulative_cost_are_read, tests/acp/test_reader.py::test_a_measured_usage_block_totals_exactly_what_the_agent_reported[opencode-2.2.5], tests/acp/test_reader.py::test_a_measured_usage_block_totals_exactly_what_the_agent_reported[claude-bridge], tests/acp/test_reader.py::test_turns_of_two_launches_in_one_log_both_count_though_their_ids_repeat, tests/acp/test_reader.py::test_the_cost_of_two_launches_in_one_log_is_summed_per_agent_session[a-resumed-session-is-cumulative], tests/acp/test_reader.py::test_the_cost_of_two_launches_in_one_log_is_summed_per_agent_session[a-fresh-session-on-a-second-launch-adds], tests/acp/test_reader.py::test_a_cancelled_turn_with_a_cost_and_no_prompt_usage_keeps_its_cost[cost-only], tests/acp/test_reader.py::test_context_size_is_never_counted_as_tokens_or_cost, tests/acp/test_reader.py::test_replay_and_foreign_currency_are_not_counted[replayed-history-skipped]
origin: src/kraft/usage.py §_stream_acp

## REQ acp-launch-refuses-a-cap-it-cannot-count
IF a token cap is in effect and the ACP entry does not report tokens, or a dollar cap is in effect and the entry reports neither a cost nor tokens on a model `prices.json` prices, THEN the system SHALL refuse the launch before it starts, naming the entry and its `reports`, whatever launched it (a chain task, an escalation turn, a gate review or a review reply) and whether the dollar cap is the instance's or the item's own.
enforced-by: tests/acp/test_launch.py::test_a_cap_the_agent_cannot_be_counted_against_refuses_the_launch[tokens-cap-no-report], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[walk-instance-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[walk-the-items-own-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[escalation-instance-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[escalation-the-items-own-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[gate_review-instance-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[gate_review-the-items-own-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[review_reply-instance-cap], tests/executor/test_dispatch_acp_caps.py::test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing[review_reply-the-items-own-cap], tests/acp/test_launch.py::test_a_cap_the_agent_cannot_be_counted_against_refuses_the_launch[usd-cap-unpriced], tests/adapters/test_agent_acp.py::test_a_launch_under_a_cap_its_entry_does_not_report_is_refused_before_it_starts
origin: src/kraft/acp/launch.py §cap_refusal

## REQ acp-unknown-spend-is-unknown-under-every-dollar-cap
IF a finished ACP session that reached a prompt reported no cost, THEN the system SHALL treat its spend as unknown under a per-scope `budget_usd` and `budget.work_item_usd`, and under `budget.daily_usd` SHALL refuse ACP launches only; it SHALL NOT count such a session as $0, nor write a `spend_unpriced` event for it, and a session that never reached a prompt SHALL NOT be unknown spend; a session's tokens on a model `prices.json` prices settle to a derived cost, so it is not unknown. Whether a session is an ACP session is what the session row recorded when it launched, so removing or renaming the harness later cannot turn its unknown spend into $0.
enforced-by: tests/executor/test_budget_caps.py::test_an_acp_session_with_no_cost_is_unknown_spend_even_at_zero_tokens[acp-zero-tokens], tests/executor/test_budget_caps.py::test_an_acp_session_with_no_cost_is_unknown_spend_even_at_zero_tokens[acp-never-reached-a-prompt], tests/executor/test_budget_caps.py::test_an_acp_sessions_unknown_cost_stops_the_item_dollar_cap_too, tests/executor/test_budget_caps.py::test_a_session_that_never_reached_a_prompt_does_not_make_the_item_unknown, tests/executor/test_budget_caps.py::test_an_acp_turn_that_reported_tokens_and_no_cost_is_priced_or_unknown_never_free[tokens-on-a-priced-model-are-priced-scope], tests/executor/test_budget_caps.py::test_an_acp_turn_that_reported_tokens_and_no_cost_is_priced_or_unknown_never_free[tokens-on-a-priced-model-are-priced-instance], tests/executor/test_budget_caps.py::test_an_acp_turn_that_reported_tokens_and_no_cost_is_priced_or_unknown_never_free[tokens-on-no-model-are-unknown-scope], tests/executor/test_budget_caps.py::test_an_acp_turn_that_reported_tokens_and_no_cost_is_priced_or_unknown_never_free[tokens-on-no-model-are-unknown-instance], tests/adapters/test_agent_acp.py::test_an_acp_launch_is_refused_under_a_daily_cap_while_an_acp_session_today_has_no_cost, tests/adapters/test_agent_acp.py::test_a_cli_launch_is_not_refused_by_the_acp_unknown_count
origin: src/kraft/caps.py §budget_breach, src/kraft/executor/stops.py §budget_breach, src/kraft/adapters/agent.py §_unknown_acp_today
