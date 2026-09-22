# Log tail

Following a session's log has to hand a reader a cursor even when there is
nothing yet to show it.

## REQ log-follow-resumes-from-next-line
WHEN a log follow request has no backlog line to resume after, the system SHALL resume from the payload's `next_line`, so a `-n 0 -f` reader prints only lines written after it started.
enforced-by: tests/test_log_tail.py::test_the_backlog_payload_says_where_the_next_line_starts, tests/cli/test_watching.py::test_logs_n_zero_follow_starts_after_the_lines_already_written
origin: src/kraft/logs.py §Tail (Kraft-tbnse)
