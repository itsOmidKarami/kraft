# Architecture

Kraft is one FastAPI process. It serves the SPA and the API, walks each work
item's chain, and spawns an agent CLI or a command for each task in the item's
git worktree. Its state is two SQLite databases under `$KRAFT_HOME/run/`:
`orchestrator.db`, the record of every work item, and `index.db`, a search
index that Kraft can rebuild at any time.

![Kraft's components](docsite/public/diagrams/components.svg)

The full page, with the request flow, trust boundaries, a map of every module
in `src/kraft/` and a "where to change things" table, lives on the docs site:
[Architecture](https://itsomidkarami.github.io/kraft/project/architecture).
Its source is
[`docsite/content/5.project/1.architecture.md`](docsite/content/5.project/1.architecture.md).

For how one work item moves from filing to merge, and its seven statuses, see
[How a work item runs](https://itsomidkarami.github.io/kraft/concepts/how-a-work-item-runs).
For the vocabulary, see
[Concepts](https://itsomidkarami.github.io/kraft/concepts/vocabulary).

The diagrams are Mermaid sources in `docsite/diagrams/`, rendered to SVG with
`just docs-diagrams`.
