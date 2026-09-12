# Never signal a process you did not start

If something is already listening on a port you need, that is not a stale
leftover to clear — it might be the Kraft daemon serving other work right
now. Check `$KRAFT_DAEMON_PID` and `$KRAFT_DAEMON_PORT` in your environment
before touching anything you find on a port: if the pid or the port matches,
it is the daemon, not garbage, and `kill`, `pkill`, or piping `lsof` into
`xargs kill` would take down orchestration for every other item running on
this install — including this one.

Ask any server you start yourself for an ephemeral port (bind port 0, or
leave `KRAFT_PORT` unset) rather than reuse the daemon's. If a task genuinely
needs the daemon's own port, that is a question for a human, not something to
resolve by killing what is already there.
