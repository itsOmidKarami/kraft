# Sub-agents in the session log

A Claude Code sub-agent's log rows say which sub-agent wrote them; a background job's never do.

## REQ a-background-job-is-never-a-subagent
WHEN a log line is a background job's (a `task_*` line with no `task_type: local_agent`, for an id never seen as a sub-agent's), the system SHALL leave its row untagged and summarised as before.
enforced-by: tests/test_logs.py::test_summary[job-started], tests/test_logs.py::test_summary[job-notification]
origin: src/kraft/logs.py §agent_fields
