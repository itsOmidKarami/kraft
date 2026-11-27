---
title: "What's new in 2.6: one plan, built in slices"
description: Several agents can now build one plan at once and still open one merge request, and in the default chain a reviewer checks every gate before it asks you.
date: 2026-11-17
author: Omid Karami
image: /blog/whats-new-in-2-6/slices.png
tags: [release]
---

Kraft 2.6 is out. Several agents can now build one large plan at once and still open a single merge request, and a reviewer agent checks every gate in the default chain before you see it.

## Build a plan in slices

Until 2.6, one implementer built each work item. A plan with three parts that never touch each other still went through one implementer, one worktree and one review.

Now a plan can name the parts that stand alone. End it with a `## Slices` section, one bullet per slice:

```md
## Slices

- **Slice 1: Parser.** Tasks 1-3.
- **Slice 2: CLI verb.** Tasks 4, 6.
```

Task 5, in no slice, is shared groundwork.

When you approve that plan, Kraft adds the split to the chain revision and asks you to approve that too. Then:

1. **Shared groundwork comes first.** Kraft builds, tests and reviews every task in no slice in the item's own worktree, so each slice starts from tested code.
2. **Each slice becomes a work item of its own,** with its own worktree, branch, implementer and review, and the slices run in parallel. You can steer, pause, retry or drop one by its ID; a slice that fails or needs you doesn't stop the others.
3. **The slices join.** When every slice has ended, Kraft replays each completed slice's commits onto the parent's branch in slice order. Verification, review and the merge request then run once, on the combined code.

The item page draws the whole group: the groundwork, a box per slice that opens its item, and the join.

::blog-animation
---
name: slices
still: /blog/whats-new-in-2-6/slices.png
alt: "An illustration of a sliced work item on the item page: the groundwork runs, then three slices run side by side. A zoom into slice 1 shows its own chain, implementation then verification. Slice 3 stops for a person and carries on, and the join replays each slice onto one branch."
---
::

If two slices conflict at the join, an agent tries to resolve the conflict. Kraft carries on only once every commit of the slice is on the branch. Otherwise it puts the branch back and stops the item, with the exact steps for you.

The item's dollar and token [caps](/concepts/vocabulary#cap) cover it and all its slices together, so slicing doesn't raise the item's cap. A cap stops the next task from starting, so slices already running can each finish their current one.

**When not to slice.** Each slice pays for its own worktree, setup, implementer and review. That's worth it only when each part stands on its own, with its own tests. For small independent tasks, one implementer with sub-agents has far less overhead. A plan without a `## Slices` section runs as one item, as before. [Split work into slices](/guides/day-to-day/split-work-into-slices) has the rules.

## A reviewer at every gate

In the `default` chain, a reviewer agent now checks each gate (spec, plan, chain revision, local review and final review) before you're asked. It reads the document the gate is about, sends work that isn't ready back with its reasons, and hands you the rest marked **ready for you**, with a note. It never approves; the gate then waits for you.

::blog-animation
---
name: gate
still: /blog/whats-new-in-2-6/gate.png
alt: "An illustration of the plan gate with its reviewer: the gate waits for you while its auto_review step checks the plan first. In round 1 the reviewer sends the plan back; in round 2 it marks the revised plan ready for you. The gate's history lists both rounds."
---
::

The gate's page also keeps its history: every round, how it ended, who decided and the note, on the desktop and the phone.

Each check is a paid agent session, at every gate an item reaches. Turn it off for one item with `kraft item create --no-auto-gate`, or for one gate with an override; [Change or turn off the gate reviewer](/guides/customize/add-review-agents#change-or-turn-off-the-gate-reviewer-in-the-default-chain) shows how.

## See what each sub-agent is doing

When a Claude Code task starts sub-agents, they used to run out of sight. Now the item, the chain graph and the phone list each one with what it's doing, its tokens and its tool uses, and the log can show one sub-agent's lines alone. [A sub-agent](/reference/web-ui/item-page#a-sub-agent) says what each field means.

::blog-animation
---
name: subagents
still: /blog/whats-new-in-2-6/subagents.png
alt: "An illustration of an implement task that starts three sub-agents: the task's pane lists each with its tokens, tool uses and the tool it runs now, a mark under the task's box follows each one, and they finish one by one."
---
::

## Also in 2.6

- **Kraft's own chains ship with Kraft.** As the built-in add-on `kraft-default`, they update when Kraft does, and `kraft admin update` shows what changes in them before it installs. See [the built-in kraft-default](/reference/configuration/addons#the-built-in-kraft-default).
- **Re-run CI that failed outside the code.** When jobs fail on a download timeout or a lost runner, a fix-loop repair can re-run them with [`kraft item rerun-ci`](/reference/cli/item#item-rerun-ci) instead of the item getting stuck.
- **Heavy test suites take turns.** A test scope's [`concurrency_group`](/reference/configuration/repos#concurrency_group) runs its suite one at a time, across work items and repos, on your machine and on each remote one.
- **Easier review.** The review page counts unresolved threads, and `]` and `[` step through them.
- **Blue means running, amber means you.** The web UI and the console draw running work in blue and keep amber for what needs you.
- **The console and VS Code reach a remote Kraft** after a password sign-in. See [the console from another machine](/reference/console#from-another-machine).
- **`uvx kraft-sdlc` starts Kraft,** and Kraft runs on Python 3.15, without vector search for now.

## Before you upgrade

Check these if you have scripts or chains of your own:

- A `kraft` command run from a Claude Code session's shell is now recorded as an assistant's, not a person's. Set `KRAFT_CLIENT=cli` for a command you mean to make as yourself.
- `kraft view list` prints the status the board shows (`running`, `needs you`, `failed`). A script that reads the STATUS column should read `--json`'s `status` instead.
- A home from before 2.6 keeps its copied chains, which don't follow `kraft-default`'s updates. [Move your edits onto an extending chain](/guides/run/upgrade-your-configuration#move-your-edits-onto-an-extending-chain) to get them.
- A chain that extends `default` and gives one of its gates an `auto_review` with an `id`, `kind` or `extends` is now refused. Delete those keys and keep any `prompt`, `skill` or `harness` it sets.

Upgrade with `kraft admin update`. [Upgrade Kraft](/guides/run/upgrade-kraft) has the steps and how to roll back, and the [2.6.0 release notes](https://github.com/itsOmidKarami/kraft/releases/tag/v2.6.0) list every change and fix.
