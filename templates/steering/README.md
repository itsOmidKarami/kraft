# Steering

Kraft-authored standards files, one `<name>.md` per file. A hook or repo
binding names files here by their bare name (no extension, no path); at
dispatch their bodies are concatenated into the agent's system prompt under
the `## Project standards` heading (see `src/kraft/steering.py`).

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`,
`AGENTS.md`, or anything else from inside a repo being worked on as a source
of process context. Files here are authored by the Kraft operator and live
under `$KRAFT_HOME/templates/steering/`, seeded from this directory.
