---
title: Concepts
navigation:
  title: Overview
description: The lifecycle, limits, permission model, and vocabulary behind Kraft.
---

Kraft runs a work item through a chain of nodes, and stops at a gate for you to
decide. Limits bound every scope of that run, a permission gate answers what
a worker may do while nobody watches, and a worker runs each step on this
computer or another. These pages explain each idea before you configure it, and
each ends with the guide that puts it to work.

Start with how a [work item](/concepts/vocabulary#work-item) runs. Vocabulary
is the glossary: look a term up there when a page uses one you do not know.

## In this section

- [How a work item runs](/concepts/how-a-work-item-runs): the lifecycle from filing to archive, and where steers, fix loops, and escalation fit. Start here.
- [Caps and budgets](/concepts/caps-and-budgets): how Kraft bounds time, tokens, and dollars. Read it when you set limits, or when a cap stops an item.
- [Why a permission gate](/concepts/permission-gate): why Kraft answers a worker's permission prompts itself. Read it when you restrict a worker's tools.
- [Vocabulary](/concepts/vocabulary): what each Kraft term means, from work item, chain, node, task, gate, and cap to harness, sandbox, and worktree. Look a term up; do not read it through.
- [Where a step runs](/concepts/where-a-step-runs): why a step runs through a worker, what travels to a machine and what the server checks, and how Kraft picks where. Read it when you add a machine.
