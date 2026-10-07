---
title: Guides
navigation:
  title: Overview
description: Task-focused how-tos for driving, extending, and reaching Kraft.
---

Guides each take one task from start to finish. Pick one by what you want to do.

## Where to start

- **You have just installed Kraft and nothing is connected.** Follow [The first run](/guides/day-to-day/first-run), then [File a work item](/guides/day-to-day/file-a-work-item).
- **You work in a coding agent and want to drive Kraft from it.** Read [Use Kraft from your agent](/guides/day-to-day/agent-integration).
- **An item stopped on a cap.** Read [Raise a cap on a stopped item](/guides/day-to-day/raise-a-cap).
- **You want to change the chain, a [harness](/concepts/vocabulary#harness) or the server.** Pick from the groups below.

## In this section

### Day to day

- [The board and the web UI](/guides/day-to-day/the-board): the sidebar, search and shortcuts every screen shares, and where each screen is described.
- [The first run](/guides/day-to-day/first-run): connect your first repo with the setup steps the board shows when none is connected.
- [File a work item](/guides/day-to-day/file-a-work-item): file a work item from the board's composer or the full new-item page.
- [Use Kraft from your agent](/guides/day-to-day/agent-integration): drive Kraft with `/kraft:*` slash commands from a coding agent.
- [Reviewing a change](/guides/day-to-day/review-a-change): comment on a work item at any point, and see your feedback reach the next agent.
- [Raise a cap on a stopped item](/guides/day-to-day/raise-a-cap): raise the cap that stopped a work item from the interface or the command line, and retry it.
- [Kraft for VS Code](/guides/day-to-day/vscode): clear gates, review a branch and edit config files from the editor.
- [Kraft on a phone](/guides/day-to-day/kraft-on-a-phone): the phone layout, its bottom bar, the board's cards and the buttons on a stopped item.
- [Get notified when an item needs you](/guides/day-to-day/notifications): send a webhook or show a browser alert when an item waits at a gate or stops, and test it.

### Customize the chain

- [Write your own chain](/guides/customize/write-your-own-chain): add a lint node with an agent that fixes what it reports, a time cap, and no skipping.
- [Add a security review or a gate reviewer](/guides/customize/add-review-agents): put the shipped `security-review` and `gate-review` skills into a chain of your own.
- [Schedule or webhook work](/guides/customize/schedule-and-webhook-work): file paused work items from a cron schedule or an HTTP call.
- [Upgrade your configuration](/guides/customize/upgrading-templates): take new shipped chains, library tasks and policy after an upgrade without losing your edits.

### Agents and harnesses

- [Switch a task to another harness](/guides/harnesses/switch-harness): run agent tasks on Codex or another CLI instead of Claude Code.
- [Add or override a harness](/guides/harnesses/adding-a-harness): add an agent CLI with a YAML file.
- [Build a worker Kit](/guides/harnesses/worker-kit): build a Docker Sandbox Kit for Claude workers and run a repository's sandbox from it.

### Run the server

- [Remote access](/guides/run/remote-access): approve or reject a gate from a phone, over a tunnel.
- [Operate a Kraft server](/guides/run/operations): what to know before you back up the database, read logs or run Kraft as a service.
- [Back up and restore the database](/guides/run/back-up-and-restore): copy the database while the server runs, restore it, and use the copy Kraft makes before an upgrade.
- [Logs and disk space](/guides/run/logs-and-disk-space): find the logs, and reclaim the space worktrees use.
- [Run Kraft as a service](/guides/run/run-as-a-service): start Kraft at login, and run a second instance.
- [Upgrade Kraft](/guides/run/upgrade-kraft): back up, upgrade and check an installed Kraft, and pin a version.
- [Upgrade from 1.4 or an older release](/guides/run/upgrade-from-1-4): what 2.0 changes from 1.4, the steps to update from it, and how to upgrade from a release candidate or an older release.
- [Roll back to 1.4](/guides/run/roll-back): what a rollback does to the database and config, and the steps from 2.0 back to 1.4.

### Without a server

- [Kraft Lite](/guides/kraft-lite): run a chain inside one agent session with no service.
