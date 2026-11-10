# Architecture

Kraft is one FastAPI process and a local worker beside it. The server serves
the SPA and the API and walks each work item's chain; the local worker starts
the agent CLI or the command for each local task in the item's git worktree.
Its state is two SQLite databases under `$KRAFT_HOME/run/`:
`orchestrator.db`, the record of every work item, and `index.db`, a search
index that Kraft can rebuild at any time.

The full description lives in one file:
[`docsite/content/6.project/5.architecture.md`](docsite/content/6.project/5.architecture.md),
published as [Architecture](https://kraft-sdlc.dev/project/architecture). It has
a diagram of the parts, how a work item moves from filing to merge, what a
restart does to running items, the trust boundaries and what is kept on disk.
It also covers remote workers (`src/kraft/remote/`) and add-ons
(`src/kraft/addons/`). Edit it there; this file stays a summary.

Related pages, in the same folder:

- [`6.source-map.md`](docsite/content/6.project/6.source-map.md), published as
  [Source map](https://kraft-sdlc.dev/project/source-map): every module in
  `src/kraft/`, the "where to change things" table, and the closed sets (how to
  add a status or an event type).
- [How a work item runs](https://kraft-sdlc.dev/concepts/how-a-work-item-runs)
  and its seven statuses.
- [Vocabulary](https://kraft-sdlc.dev/concepts/vocabulary).

The docs site's diagrams are written in the pages' Markdown and drawn by the
`Diagram*.vue` components in `docsite/app/components/`;
[`docs/writing-docs.md`](docs/writing-docs.md) says how.
