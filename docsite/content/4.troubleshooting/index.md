---
title: Troubleshooting
navigation:
  title: Overview
description: Where to look when a work item stopped, the server won't start, a doctor row fails, or an add-on is refused.
---

Start from what you see on screen, then pick the topic page that holds it.

## Find your reason

An item under **Needs you** shows a reason.

1. Select the item on the board, or run `kraft view show ID`.
2. Match the first words of the reason to the first column of a page below.
3. Follow the Fix column. Most fixes end in `kraft item retry ID`.

Read the reason first. A retry that skips the cause runs into the same failure.

![The board's Needs you group: seven items, each with its stop reason under the title and one button such as Review, Retry, Raise cap, Answer or Resume](/assets/stops.png)

The item's side panel says where it stopped and why, with the one action that fits, and lists what happened last:

![A stopped item's side panel: Stopped at verification, Running time hit its 8h cap, then its status, spend and recent events](/assets/stop-peek.png)

Two more commands show more: `kraft view events ID --type work_item_needs_human` prints the stop with every field, and `kraft view logs ID` the log of the session that stopped it. A retry resets the node's attempt counters, and `kraft item retry ID --steer "..."` tells the agent what to do differently. In Claude Code, `/kraft:triage` reads all three and suggests the next step.

A pending gate isn't a stop: it waits for `kraft item approve ID` or `kraft item reject ID --note "..."`. API users: the stop kinds and their fields are in the [events reference](/reference/events#stop-kinds).

## In this section

- [Failed tasks and fix loops](/troubleshooting/failed-tasks-and-fix-loops): `task failed in node ...`, `needs_context: ...`, `... exhausted`, `stuck: ...`, a closed merge request, a crash and a parent that waits on its slices.
- [Launch and setup stops](/troubleshooting/launch-and-setup-stops): `could not start ...`, `no setup_command declared ...` and `no user.name configured ...`, before any agent ran.
- [Caps, budgets and waits](/troubleshooting/caps-budgets-and-waits): `budget cap reached`, `hit its time cap`, and an item that waits on CI, a review or a machine.
- [Forge stops](/troubleshooting/forge-stops): `cannot run: forge ...`, and a forge or git call that failed on a login, a rate limit, an unreachable host or a changed account.
- [Worktree and git stops](/troubleshooting/worktree-and-git-stops): a rebase conflict, a worktree git won't rebase, or a branch someone else pushed to.
- [Sandbox and Kit stops](/troubleshooting/sandbox-and-kit-stops): the memory-limit, Kit, workspace-member and nested-repository stops of a sandboxed item, and the sandbox rows of `kraft admin doctor`.
- [A kind: os sandbox is refused](/troubleshooting/os-sandbox-refusals): a host that can't run bubblewrap or Seatbelt, or a command outside the sandbox's view.
- [Doctor failures](/troubleshooting/doctor-failures): every row `kraft admin doctor` can print, and how to clear it.
- [Starting and reaching Kraft](/troubleshooting/starting-and-reaching-kraft): a missing `kraft` command, a taken port, and a rejected `Host`.
- [Add-on problems](/troubleshooting/addon-problems): `add-on ... could not be read`, and what `kraft admin addon` prints when it refuses a collection, install, update or removal.
- [Add-on validation messages](/troubleshooting/addon-validate-messages): what `kraft admin addon validate` reports in an add-on you wrote.

## Other places to look
<!-- not new: the last rows of the Find your problem table -->

- A page that isn't where you left it, or a theme that looks different, after an upgrade from 1.4: [Where the pages moved](/reference/changes-from-1-4#where-the-pages-moved).
- A question about [forges](/concepts/vocabulary#forge), [harnesses](/concepts/vocabulary#harness), cost, platforms, specs, sharing chains with add-ons, Kraft Lite or the permission gate: the [FAQ](/get-started/faq).
- None of these matches: [Getting help](/project/status-and-support#getting-help).
