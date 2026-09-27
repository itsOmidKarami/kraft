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

## REQ exit-replaces-the-estimate-and-never-leaves-it-standing
WHEN a session exits or is paused, the system SHALL overwrite any estimated `cost_usd` with the agent's own reported figure (or `NULL` when the agent reported none) and SHALL clear `cost_estimated`, so an estimate never survives as if it were the session's final cost.
enforced-by: tests/store/test_sessions.py::test_exit_replaces_the_estimate_with_the_real_cost_and_clears_the_flag, tests/store/test_sessions.py::test_exit_with_no_reported_cost_does_not_leave_the_estimate_standing, tests/store/test_sessions.py::test_pause_also_clears_a_running_estimate
origin: src/kraft/store/sessions.py §session_exited, §record_pause_usage (Kraft-wz83s)

## REQ budget-counts-a-running-sessions-estimated-spend
WHILE a work item has a running session with an estimated cost, the system SHALL include that estimate in `budget_spend` and in `caps.budget_breach`, so a dollar cap can stop a launch before the session that would breach it has exited.
enforced-by: tests/store/test_budget.py::test_budget_spend_includes_a_running_sessions_estimate, tests/executor/test_budget_caps.py::test_a_running_sessions_estimate_trips_a_usd_cap
origin: src/kraft/store/budget.py §budget_spend, src/kraft/caps.py §budget_breach (Kraft-wz83s)

## REQ rollups-expose-that-a-total-includes-an-estimate
WHEN any session folded into a usage rollup is still running on an estimated cost, the system SHALL mark that rollup's total `cost_estimated`, distinct from `cost_complete`, so a reader can render the figure as an estimate rather than a settled total.
enforced-by: tests/test_usage.py::test_rollup_marks_the_total_estimated_when_a_running_session_has_a_guess
origin: src/kraft/store/budget.py §usage_rollup (Kraft-wz83s)
