# Agent Instructions

The build, test, run, and worker instructions for this repository are in
[CLAUDE.md](CLAUDE.md). Read it; it is the single source and applies to every
agent. If `AGENTS.local.md` exists beside this file, read it too: it holds the
maintainer's private issue-tracker workflow and is not part of the public
repository.

## Kraft Workers

A session with `$KRAFT_WORK_ITEM_ID` set is a Kraft worker, running in a
throwaway git worktree on its own branch. It commits everything it changes
before it exits — uncommitted work never reaches the merge request and is
destroyed with the worktree. This overrides the Conservative profile's
"do not run git commits" for commits only: a worker still does not push,
merge, sync Dolt, or close beads. Kraft does those itself. The full worker
rules, including the environment allowlist, are in [CLAUDE.md](CLAUDE.md).
