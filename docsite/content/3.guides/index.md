---
title: Guides
navigation:
  title: Overview
description: Task-focused how-tos for driving, extending, and reaching Kraft.
---

Each guide walks through one job from start to finish.

- [Kraft Lite](/guides/kraft-lite): run a chain inside one agent session with no service.

## Day to day

- [The board and the web UI](/guides/day-to-day/the-board): where each screen of the web UI is described, from the board and an item's page to review, Templates, Settings and the phone.
- [The first run](/guides/day-to-day/first-run): the setup steps the board shows when no repo is connected.
- [File a work item](/guides/day-to-day/file-a-work-item): file a work item from the board's composer or the full new-item page.
- [Use Kraft from your agent](/guides/day-to-day/agent-integration): drive Kraft with `/kraft:*` slash commands from a coding agent.
- [Reviewing a change](/guides/day-to-day/review-a-change): comment on a work item at any point, and see your feedback reach the next agent.
- [Raise a cap on a stopped item](/guides/day-to-day/raise-a-cap): raise the cap that stopped a work item from the interface or the command line, and retry it.
- [Kraft for VS Code](/guides/day-to-day/vscode): clear gates, review a branch and edit config files from the editor.
- [Kraft on a phone](/guides/day-to-day/kraft-on-a-phone): the phone layout, its bottom bar, the board's cards and the buttons on a stopped item.

## Customize the chain

- [Write your own chain](/guides/customize/write-your-own-chain): add a lint node with an agent that fixes what it reports, a time cap, and no skipping.
- [Add a security review or a gate reviewer](/guides/customize/add-review-agents): put the shipped `security-review` and `gate-review` skills into a chain of your own.
- [Schedule or webhook work](/guides/customize/schedule-and-webhook-work): file paused work items from a cron schedule or an HTTP call.
- [Upgrade your templates](/guides/customize/upgrading-templates): take new shipped chains and library tasks after an upgrade without losing your edits.

## Agents and harnesses

- [Switch a task to another harness](/guides/harnesses/switch-harness): run agent tasks on Codex or another CLI instead of Claude Code.
- [Add or override a harness](/guides/harnesses/adding-a-harness): add an agent CLI with a YAML file.
- [Build a worker Kit](/guides/harnesses/worker-kit): build a Docker Sandbox Kit for Claude workers and run a repository's sandbox from it.

## Run the server

- [Remote access](/guides/run/remote-access): approve or reject a gate from a phone, over a tunnel.
- [Operate a Kraft server](/guides/run/operations): back up the database, find logs, reclaim disk space, run two instances, and run Kraft as a service.
