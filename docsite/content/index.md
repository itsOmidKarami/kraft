---
title: Run your coding agent from spec to pull request
description: 'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.'
---

::u-page-hero
---
headline: '$ uv tool install kraft-sdlc && kraft'
title: 'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.'
description: "Use Kraft when you want agent work to run while you are not watching, and to stop only where a person should decide. Use an interactive session for everything else. Hand Claude Code (or another coding agent) a spec and walk away. It's for developers tired of babysitting a session to the end of a task. Retries and spend are capped."
links:
  - label: Get started
    to: /get-started
    color: primary
  - label: View on GitHub
    to: https://github.com/itsOmidKarami/kraft
    variant: outline
    color: neutral
    target: _blank
ui:
  wrapper: text-left items-start
  headline: hero-mono normal-case font-normal tracking-normal justify-start
  title: hero-mono text-left
  description: text-left
  links: justify-start
---

#body
::stage-strip
::
::

::u-page-section
---
title: The board
description: Work items grouped by what needs you, what is running, and what is done — redrawn live as agents work.
orientation: horizontal
---
![The Kraft board: work items grouped by Needs you, Running, Not started, and Done](/assets/board.png)
::

::u-page-section
---
title: A gate stops the chain where a human decides
description: Read what the agent wrote, then approve, or reject with a note that re-runs the node that wrote it.
orientation: horizontal
reverse: true
---
![A spec an agent wrote, waiting for your approval in the item's detail view](/assets/gate.png)
::

::u-page-section
---
title: Search, ⌘K
description: Full-text search, with optional vector search, across work items, pending actions, and linked documents.
orientation: horizontal
---
![The search overlay: a query for "CSV" surfacing a pending approval, the matching work items, and agent session summaries](/assets/search.png)
::

::u-page-section
---
title: Analytics
description: Lead time, cost, and where both go — by node, by repo, over whatever window you pick. Built from the same events the board renders live, not a separate pipeline.
orientation: horizontal
reverse: true
---
![The Analytics view: completed count, median lead time, cost; throughput by week; cost share by node; per-repo totals](/assets/analytics.png)
::

::u-page-section
---
title: Start here
ui:
  wrapper: text-left items-start
  title: text-left
---
:::u-page-list
---
divide: true
class: max-w-2xl
---
::::u-page-card
---
to: /get-started/why-kraft
title: Why Kraft
description: What it does that a session, a loop or a skill does not, and when not to use it.
variant: ghost
---
::::

::::u-page-card
---
to: /get-started
title: Get started
description: Install Kraft and run your first work item.
variant: ghost
---
::::

::::u-page-card
---
to: /concepts
title: Concepts
description: Vocabulary, caps and budgets, and why Kraft has a permission gate.
variant: ghost
---
::::

::::u-page-card
---
to: /guides
title: Guides
description: Use Kraft from your agent, run Kraft Lite, reach the board from a phone, add a harness.
variant: ghost
---
::::

::::u-page-card
---
to: /reference
title: Reference
description: The CLI, every configuration file, chain nodes, permissions, harnesses, and triggers.
variant: ghost
---
::::
:::
::

::u-page-section
---
title: Source
description: >-
  Kraft is on GitHub, Apache-2.0 licensed. CONTRIBUTING.md covers getting a
  dev environment running.
links:
  - label: itsOmidKarami/kraft
    to: https://github.com/itsOmidKarami/kraft
    target: _blank
    color: neutral
    variant: outline
  - label: CONTRIBUTING.md
    to: https://github.com/itsOmidKarami/kraft/blob/main/CONTRIBUTING.md
    target: _blank
    color: neutral
    variant: outline
ui:
  wrapper: text-left items-start
  title: text-left
  description: text-left
  links: justify-start
---
::
