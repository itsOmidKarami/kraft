# Agent Instructions

The build, test, run, and worker instructions for this repository are in
[CLAUDE.md](CLAUDE.md). Read it; it is the single source and applies to every
agent. If `AGENTS.local.md` exists beside this file, read it too: it holds the
maintainer's private issue-tracker workflow and is not part of the public
repository.

## Kraft Workers

A session with `$KRAFT_WORK_ITEM_ID` set is a Kraft worker: it commits
everything it changes before it exits, and leaves pushing, merging and the
issue tracker to Kraft. The rules are in [CLAUDE.md](CLAUDE.md#kraft-workers).

## Docs

A new docs page, section or CLI verb carries `_Added in next_` under its
heading, and never a version number: see [CLAUDE.md](CLAUDE.md#docs-mark-what-is-new).
