---
title: Kraft
---

::u-page-hero
---
headline: '$ uv tool install kraft-sdlc && kraft'
title: 'Work items become chains. Chains run themselves until a human has to decide.'
description: 'Runs on your machine and edits your repos through ordinary git worktrees. It only stops to ask when a decision needs a person.'
links:
  - label: Read the guide
    to: /guide/getting-started
    color: primary
  - label: View source
    to: https://github.com/itsOmidKarami/kraft
    variant: link
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
description: Approve, reject with a note that re-runs the node that wrote the document, or open the full detail view.
orientation: horizontal
reverse: true
---
![Approving a spec_approval gate from the board's side panel](/assets/gate.png)
::

::u-page-section
---
title: Search, ⌘K
description: Full-text search, with optional vector search, across work items, pending actions, and linked documents.
orientation: horizontal
---
![The search overlay: a query for "caching" surfacing a pending gate action, the matching work item, and a source-repo attribution](/assets/search.png)
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
to: /guide/getting-started
title: Getting started
description: Install, connect a repo, run a work item end to end, approve its gate.
variant: ghost
---
::::

::::u-page-card
---
to: /guide/concepts
title: Concepts
description: 'Work item, chain, node, task, gate, cap: the whole vocabulary.'
variant: ghost
---
::::

::::u-page-card
---
to: /reference/install
title: Install
description: 'With uv, the install script, or Homebrew.'
variant: ghost
---
::::

::::u-page-card
---
to: /reference/cli
title: CLI reference
description: Every kraft verb, grouped by what it does.
variant: ghost
---
::::

::::u-page-card
---
to: /reference/configuration
title: Configuration
description: Every field in library.yaml, repos.yaml, policy.yaml, access.yaml.
variant: ghost
---
::::

::::u-page-card
---
to: /reference/agent-integration
title: Agent integration
description: Driving Kraft with /kraft:* slash commands from a coding agent instead of the browser.
variant: ghost
---
::::

::::u-page-card
---
to: /reference/remote-access
title: Remote access
description: Approving a gate from your phone.
variant: ghost
---
::::

::::u-page-card
---
to: /reference/triggers
title: Inbound triggers
description: Starting a chain from a cron schedule or a webhook instead of filing it by hand.
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
  dev environment running and how releases work.
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
