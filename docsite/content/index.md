---
title: Run your coding agent from spec to pull request
description: 'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.'
---

::u-page-hero
---
title: 'Run your coding agent from spec to pull request.'
description: "A local orchestrator. Kraft isn't another coding agent: it runs the one you already use, and stops only when a decision is yours."
links:
  - label: Get started
    to: /get-started
    color: primary
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

It adds what a single session can't:

- **A process the agent can't skip.** A chain like the one above is a file you can read and diff; the agent does not decide the workflow fresh each time.
- **Checks it doesn't grade itself on.** Kraft runs your tests in a node of its own, waits for your CI itself, and has a separate reviewer or judge decide whether a fix is done.
- **A person at the decisions that matter.** You approve a spec, a plan, or a merge request. Hand it a spec and walk away; retries and spend are capped.
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
alt: 'The Kraft board in the dark graphite look: work items grouped by Needs you (a paused item with Resume, a failed one with Retry, and two waiting at a gate, each with its one button), Running, Not started, and Done, the unstarted items marked created paused, with a Select all link on every group and a ? shortcuts button in the toolbar'
crop: 210 0 1230 688
width: 1440
---
:::
::

::u-page-section
---
title: A gate stops the chain where a person decides
description: Read what the agent wrote, then approve, or reject with a note that re-runs the node that wrote it.
ui:
  root: landing
---
::::div{.landing-shots}
:::::landing-shot
---
src: /assets/item.png
alt: "The spec_approval gate on an item's page: waiting for you, the files it decides on, an open thread, reject to spec, and Read spec.md, Review changes, Approve and Reject buttons"
crop: 1036 250 380 434
width: 1440
---
**The gate.** What it decides on, where a rejection goes, and the buttons. [The review page](/reference/web-ui/review-page)
:::::

:::::landing-shot
---
src: /assets/review.png
alt: 'A line comment on a one-line change, labelled Must fix, being added to a review'
crop: 500 256 560 380
width: 1440
---
**The note.** Comment on the lines it is about, and label it must-fix, question, or nit. [Review a change](/guides/day-to-day/review-a-change)
:::::

:::::landing-shot
---
src: /assets/mobile.png
alt: 'The board at a 390px phone viewport: a failed item with a Retry button, and an item waiting at approve spec with a Review to approve button on the card'
crop: 0 330 390 444
width: 390
---
**On your phone.** The same board at the same address, laid out for a phone. Open a gate's review from the card's one button, and answer a question or retry a stopped item from its page. [The phone layout](/guides/day-to-day/kraft-on-a-phone) · [Reach it from your phone](/guides/run/remote-access)
:::::
::::

- **Search, ⌘K.** Full-text search, with optional vector search, across work items, pending actions, and linked documents.
- **Analytics.** See where lead time and cost go, by node and by repo, over the last 8 weeks (on a phone, the last 7, 30 or 90 days). [The Analytics screen](/reference/web-ui/analytics)
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
[Why Kraft](/get-started/why-kraft) · [Concepts](/concepts) · [Source on GitHub](https://github.com/itsOmidKarami/kraft){target="_blank"}, Apache-2.0 licensed; CONTRIBUTING.md covers getting a dev environment running.
:::
::
