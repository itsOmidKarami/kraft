---
title: Concepts
navigation:
  title: Overview
description: The lifecycle, limits, permission model, where a step runs, and vocabulary behind Kraft.
---

Kraft runs a work item through a chain of nodes, and stops at a gate for you to
decide. Limits bound every scope of that run, a permission gate answers what
a worker may do while nobody watches, and a step can run on this computer or
another. These pages explain each idea before you configure it, and
each ends with the guide that puts it to work.

Start with how a [work item](/concepts/vocabulary#work-item) runs. Vocabulary
is the glossary: look a term up there when a page uses one you don't know.

## In this section

- [How a work item runs](/concepts/how-a-work-item-runs): the lifecycle from filing to archive, and where steers, fix loops and escalation fit. Start here.
- [Caps and budgets](/concepts/caps-and-budgets): how Kraft bounds time, tokens and dollars. Read it when you set limits, or when a cap stops an item.
- [Why a permission gate](/concepts/permission-gate): why Kraft answers a worker's permission prompts itself. Read it when you restrict a worker's tools.
- [Vocabulary](/concepts/vocabulary): what each Kraft term means, from work item, chain, node, task, gate and cap to harness, sandbox and worktree. Look a term up; don't read it through.
- [Where a step runs](/concepts/where-a-step-runs): why a step runs through a worker, what travels to a machine and what a worker is trusted with, and how Kraft picks where. Read it when you add a machine.
- [How review and CI waits settle](/concepts/how-review-and-ci-waits-settle): when a review bot counts as finished with a push, what the wait does with what it said, and when it ends. Read it when a review wait holds or settles early.
- [How test scopes run](/concepts/how-test-scopes-run): what a targeted test command is handed, how scopes sharing a group queue, and how test-selection state carries between worktrees. Read it when verification runs more or less than you expect.
- [How workers and machines behave](/concepts/how-workers-and-machines-behave): how the local worker and a machine take steps, share slots and groups, go offline and carry on without the server. Read it when a step waits for a machine or a machine drops out.
