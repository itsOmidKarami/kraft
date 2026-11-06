# Sub-agents in the session log

A Claude Code sub-agent's log rows say which sub-agent wrote them; a background job's never do.

## REQ a-background-job-is-never-a-subagent
WHEN a log line is a background job's (a `task_*` line with no `task_type: local_agent`, for an id never seen as a sub-agent's), the system SHALL leave its row untagged and summarised as before.
enforced-by: tests/test_logs.py::test_summary[job-started], tests/test_logs.py::test_summary[job-notification]
origin: src/kraft/logs.py §agent_fields

## REQ a-cut-off-subagent-does-not-fail-the-session
WHEN a session ends with a sub-agent that never reported back, the system SHALL keep the session's status, SHALL name it in one log note on normal, capped and idle ends, and SHALL NOT emit an event.
enforced-by: tests/adapters/test_background_jobs.py::test_a_cut_off_subagent_is_noted_not_failed[done], tests/adapters/test_background_jobs.py::test_a_cut_off_subagent_is_noted_not_failed[no-result-file], tests/adapters/test_background_jobs.py::test_a_job_and_a_cut_off_subagent, tests/adapters/test_background_jobs.py::test_two_cut_off_subagents_share_one_note, tests/adapters/test_background_jobs.py::test_a_paused_session_gets_no_note, tests/adapters/test_background_jobs.py::test_the_note_on_a_time_cap, tests/adapters/test_background_jobs.py::test_a_session_adopted_with_a_cut_off_subagent_is_noted, tests/adapters/test_background_jobs.py::test_an_adopted_session_stopped_at_its_cap_is_noted
origin: src/kraft/adapters/subprocess.py §note_cut_off_subagents
