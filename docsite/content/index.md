---
title: Run your coding agent from spec to merge request, stopping only when a decision is yours
description: 'A local orchestrator that takes your coding agent from spec to merge request, stopping only when a decision is yours.'
---

::u-page-hero
---
title: 'Run your coding agent from spec to merge request, stopping only when a decision is yours.'
description: "A local orchestrator. Kraft isn't another coding agent: it runs Claude Code, the one you already use, and adds the process, the checks and the approvals a single session can't."
links:
  - label: Get started
    to: /get-started/install
    color: primary
    size: lg
  - label: Try the demo
    to: /demo/
    variant: outline
    color: neutral
    size: lg
  - label: View on GitHub
    to: https://github.com/itsOmidKarami/kraft
    variant: outline
    color: neutral
    size: lg
    target: _blank
ui:
  root: landing landing-hero
  title: hero-mono
---

#body
```bash
uv tool install kraft-sdlc && kraft
```

#default
:::stage-strip
:::

Kraft needs Claude Code installed and signed in. The shipped chains run every agent task on it; [other agents](/reference/harnesses) take over a task after a chain edit.

Three things set it apart:

- **A process the agent can't skip.** A chain like the one above is a file you can read and diff, so the agent doesn't decide the workflow fresh each time.
- **Checks it doesn't grade itself on.** Kraft runs your tests in a node of its own, waits for your CI itself and has a separate reviewer or judge decide whether a fix is done.
- **A person at the decisions that matter.** A gate stops the chain until a decision is recorded. Only a sandbox with a `network:` policy keeps an agent from deciding its own gates. Without a sandbox, the default, Kraft still refuses an agent's calls to decide them, but the agent runs as you and can get around that. [Security](/project/security-in-depth#agents-and-gates) has the scope.

It's for developers tired of babysitting a session. [When not to use Kraft](/get-started/why-kraft#when-not-to-use-kraft) says who it isn't for.
::

::u-page-section
---
title: The board
description: Work items grouped by what needs you, what is running, and what is done, redrawn live as agents work.
links:
  - label: Tour the web UI
    to: /reference/web-ui/board
    color: neutral
    variant: outline
ui:
  root: landing
---
:::landing-shot
---
src: /assets/board.png
alt: 'The Kraft board: work items grouped by status with Needs you first, each with one button such as Review, Retry or Raise cap, and Running below'
crop: 210 0 1230 688
width: 1440
---
:::
::

::u-page-section
---
title: A gate stops the chain where a person decides
description: Read what the agent wrote, then approve, or request changes with a note that re-runs the node that wrote it.
ui:
  root: landing
---
::::div{.landing-shots}
:::::landing-shot
---
src: /assets/item.png
alt: "The final_review gate on an item's page: waiting for you, the files it decides on, an open thread, and Read kraft-cb59-review.md, Review gate, Approve and Request changes buttons"
crop: 1036 262 380 422
width: 1440
---
**The gate.** What it decides on, where a rejection goes, and the buttons. [The review page](/reference/web-ui/review-page)
:::::

:::::landing-shot
---
src: /assets/review.png
alt: 'A line comment on a one-line change, labelled Must fix, being added to a review'
crop: 500 196 924 244
width: 1440
---
**The note.** Comment on the lines it is about, and label it must-fix, question or nit. [Review a change](/guides/day-to-day/review-a-change)
:::::

:::::landing-shot
---
src: /assets/mobile.png
alt: 'The board at a 390px phone viewport: an item waiting at approve final review with a Review button on the card, and a stopped item with a Retry button'
crop: 0 212 390 430
width: 390
---
**On your phone.** The same board at the same address, laid out for a phone. Open a gate's review from the card's one button, and answer a question or retry a stopped item from its page. [The phone layout](/guides/day-to-day/kraft-on-a-phone) · [Reach it from your phone](/guides/run/remote-access)
:::::
::::

- **Search, ⌘K.** Full-text search, with optional vector search, across work items, pending actions and linked documents.
- **Analytics.** See where lead time and cost go, by node and by repo, over the last 8 weeks by default (7 days, 30 days, 8 weeks and 90 days on desktop and phone alike). [The Analytics screen](/reference/web-ui/analytics)
::

::u-page-section
---
title: Start here
links:
  - label: Install
    to: /get-started/install
    color: primary
    size: lg
    trailing-icon: i-lucide-arrow-right
  - label: Your first work item
    to: /get-started/first-work-item
    color: primary
    variant: outline
    size: lg
ui:
  root: landing landing-start
---
#description
Kraft runs on macOS and Linux. It needs git and Claude Code, and the `default` chain also needs a GitHub or GitLab remote. [Install](/get-started/install) lists the rest. Before you run it on real code, read [Security](/project/security) and [Data and privacy](/project/data-and-privacy).

#default
:::p
[Why Kraft](/get-started/why-kraft) · [Concepts](/concepts) · [Source on GitHub](https://github.com/itsOmidKarami/kraft){target="_blank"}, Apache-2.0 licensed and built in the open: the [starter issues](https://github.com/itsOmidKarami/kraft/issues/1131){target="_blank"} are a way in, and CONTRIBUTING.md covers getting a dev environment running.
:::
::
