---
title: "What's new in 2.6: one plan, built in slices"
description: Several agents can now build one plan at once and still open one merge request, and a reviewer checks every gate before it asks you.
date: 2026-11-17
author: Omid Karami
image: /blog/whats-new-in-2-6/slices.png
tags: [release]
---

Kraft 2.6 is out. Several agents can now build one large plan at once and still open a single merge request, and a reviewer agent checks every gate in the default chain before you see it.

## Build a plan in slices

Until 2.6, one implementer built each work item. A plan with three parts that never touch each other still got built one part at a time.

Now a plan can name the parts that stand alone. End it with a `## Slices` section, one bullet per slice:

```md
## Slices

- **Slice 1: Parser.** Tasks 1-3.
- **Slice 2: CLI verb.** Tasks 4, 6.
```

When you approve that plan:

1. **Shared groundwork comes first.** Kraft builds, tests and reviews every task in no slice in the item's own worktree, so each slice starts from tested code.
2. **Each slice becomes a work item of its own,** with its own worktree, branch, implementer and review, and the slices run at the same time. You can steer, pause, retry or drop one by its ID; a slice that fails or needs you doesn't stop the others.
3. **The slices join.** When every slice has ended, Kraft replays each one's commits onto the parent's branch in slice order. Verification, review and the merge request then run once, on the combined code.

The item page draws the whole group: the groundwork, a box per slice that opens its item, and the join.

![A sliced work item's chain: the groundwork's implementation and verification, three slices side by side, then the join](/blog/whats-new-in-2-6/slices.png)

If two slices conflict at the join, an agent tries to resolve the conflict. Kraft carries on only once every commit of the slice is on the branch. Otherwise it puts the branch back and stops the item, with the exact steps for you.

One set of [caps](/concepts/vocabulary#cap) covers the item and all its slices, so slicing doesn't multiply what an item may spend.

**When not to slice.** Each slice pays for its own worktree, setup, implementer and review. That's worth it only when each part stands on its own, with its own tests. For small independent tasks, one implementer with sub-agents costs far less. A plan without a `## Slices` section runs as one item, as before. [Split work into slices](/guides/day-to-day/split-work-into-slices) has the rules.

## A reviewer at every gate

The screenshots in this section and the next come from the [demo](https://kraft-sdlc.dev/demo/), on its sample data.


In the `default` chain, a reviewer agent now checks each gate (spec, plan, chain revision, local review and final review) before you're asked. It reads the document the gate is about, sends work that isn't ready back with its reasons, and hands you the rest marked **ready for you**, with a note. It never approves: the decision stays yours.

![A gate in the demo: the auto_review step, marked checks first, runs before you; the panel lists the gate's two rounds, the first ended with changes requested](/blog/whats-new-in-2-6/gate-reviewer.png)

The gate's page also keeps its history: every round, how it ended, who decided and the note, on the desktop and the phone.

Each check is a paid agent session, at every gate an item reaches. Turn it off for one item with `kraft item create --no-auto-gate`, or for one gate with an override; [Change or turn off the gate reviewer](/guides/customize/add-review-agents#change-or-turn-off-the-gate-reviewer-in-the-default-chain) shows how.

## See what each sub-agent is doing

When a Claude Code task starts sub-agents, they used to run out of sight. Now the item, the chain graph and the phone list each one with what it's doing, its tokens and its tool uses, and the log can show one sub-agent's lines alone. [A sub-agent](/reference/web-ui/item-page#a-sub-agent) says what each field means.

![A running code review step in the demo, with one sub-agent listed: what it checks, 17.8k tokens, 11 tool uses, running Grep](/blog/whats-new-in-2-6/sub-agents.png)

## Also in 2.6

- **Kraft's own chains ship with Kraft.** As the built-in add-on `kraft-default`, they update when Kraft does, and `kraft admin update` shows what changes in them before it installs. See [the built-in kraft-default](/reference/configuration/addons#the-built-in-kraft-default).
- **Re-run CI that failed outside the code.** When jobs fail on a download timeout or a lost runner, a fix-loop repair can re-run them with [`kraft item rerun-ci`](/reference/cli/item#item-rerun-ci) instead of the item getting stuck.
- **Heavy test suites take turns.** A test scope's [`concurrency_group`](/reference/configuration/repos#concurrency_group) runs its suite one at a time, across work items and repos, on your machine and on each remote one.
- **Easier review.** The review page counts unresolved threads, and `]` and `[` step through them.
- **Blue means running, amber means you.** The web UI and the console draw running work in blue and keep amber for what needs you.
- **The console and VS Code reach a remote Kraft** after a password sign-in. See [the console from another machine](/reference/console#from-another-machine).
- **`uvx kraft-sdlc` starts Kraft,** and Kraft runs on Python 3.15, without vector search for now.

## Before you upgrade

Three changes can affect a script or a chain of your own:

- A `kraft` command run from a Claude Code session's shell is now recorded as an assistant's, not a person's. Set `KRAFT_CLIENT=cli` for a command you mean to make as yourself.
- `kraft view list` prints the status the board shows (`running`, `needs you`, `failed`). A script that reads the STATUS column should read `--json`'s `status` instead.
- A chain that extends `default` and gives one of its gates an `auto_review` with an `id`, `kind` or `extends` is now refused. Delete those keys and keep any `prompt`, `skill` or `harness` it sets.

Upgrade with `kraft admin update`. [Upgrade Kraft](/guides/run/upgrade-kraft) has the steps and how to roll back, and the [2.6.0 release notes](https://github.com/itsOmidKarami/kraft/releases/tag/v2.6.0) list every change and fix.
