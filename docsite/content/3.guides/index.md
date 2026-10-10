---
title: Guides
navigation:
  title: Overview
description: Task-focused how-tos for driving, extending, and reaching Kraft.
---

Guides each take one task from start to finish. Pick one by what you want to do.

## Where to start

- **You have just installed Kraft and nothing is connected.** Follow [Your first work item](/get-started/first-work-item) from a terminal or your agent, or [The setup wizard on first start](/guides/day-to-day/first-run) in the web UI. Then read [File a work item](/guides/day-to-day/file-a-work-item).
- **A work item is waiting at a gate for you to approve or reject, or you want to comment on one.** Read [Reviewing a change](/guides/day-to-day/review-a-change).
- **You work in a coding agent and want to drive Kraft from it.** Read [Use Kraft from your agent](/guides/day-to-day/agent-integration).
- **An item stopped on a cap.** Read [Raise a cap on a stopped item](/guides/day-to-day/raise-a-cap).
- **You want the board on your phone.** Read [Kraft on a phone](/guides/day-to-day/kraft-on-a-phone), then [Remote access](/guides/run/remote-access).
- **You are upgrading Kraft, backing it up, or going back a version.** Read [Upgrade Kraft](/guides/run/upgrade-kraft), [Back up and restore the database](/guides/run/back-up-and-restore) or [Roll back to 1.4](/guides/run/roll-back).
- **You want to change the chain, a [harness](/concepts/vocabulary#harness) or the server.** Pick from the groups below.

## In this section

### Day to day

- [The setup wizard on first start](/guides/day-to-day/first-run): connect your first repo with the setup steps the board shows when none is connected.
- [File a work item](/guides/day-to-day/file-a-work-item): file a work item from the board's composer or the full new-item page.
- [Use Kraft from your agent](/guides/day-to-day/agent-integration): drive Kraft with `/kraft:*` slash commands from a coding agent.
- [Reviewing a change](/guides/day-to-day/review-a-change): approve or reject the gate a work item waits at, in the web UI or the CLI, and comment on it at any point.
- [Raise a cap on a stopped item](/guides/day-to-day/raise-a-cap): raise the cap that stopped a work item from the interface or the command line, and retry it.
- [Kraft for VS Code](/guides/day-to-day/vscode): clear gates, review a branch and edit config files from the editor.
- [Kraft on a phone](/guides/day-to-day/kraft-on-a-phone): the phone layout, its bottom bar, the board's cards and the buttons on a stopped item.
- [Get notified when an item needs you](/guides/day-to-day/notifications): send a webhook or show a browser alert when an item waits at a gate or stops, and test it.

### Customize and automate

- [Write your own chain](/guides/customize/write-your-own-chain): add a lint node with an agent that fixes what it reports, a time cap, and no skipping.
- [Add a security review or a gate reviewer](/guides/customize/add-review-agents): put the shipped `security-review` and `gate-review` skills into a chain of your own.
- [Schedule or webhook work](/guides/customize/schedule-and-webhook-work): file paused work items from a cron schedule or an HTTP call.
- [Share chains with plugins](/guides/customize/share-chains-with-plugins): publish chains, components, skills and agent profiles as a Kraft plugin, and install, use and update one.

### Agents and harnesses

- [Switch a task to another harness](/guides/harnesses/switch-harness): run agent tasks on Codex or another CLI instead of Claude Code.
- [Add or override a harness](/guides/harnesses/adding-a-harness): add an agent CLI with a YAML file.
- [Build a worker Kit](/guides/harnesses/worker-kit): build a Docker Sandbox Kit for Claude workers and run a repository's sandbox from it.
- [Give a worker its login](/guides/harnesses/give-a-worker-its-login): give the agent CLI a worker runs on a login it can use headless, and check that it works.

### Run the server

- [Remote access](/guides/run/remote-access): reach the board from a phone or another machine, over Tailscale, your local network or a tunnel, with a password.
- [Put a proxy in front of Kraft](/guides/run/put-a-proxy-in-front-of-kraft): let a tunnel or reverse proxy on this machine pass webhooks and scripts to a loopback-bound Kraft.
- [Restart Kraft without ending running agents](/guides/run/restart-kraft): pause the items that have an agent running, restart the server, and resume them.
- [Back up and restore the database](/guides/run/back-up-and-restore): copy the database while the server runs, restore it, and use the copy Kraft makes before an upgrade.
- [Logs and disk space](/guides/run/logs-and-disk-space): find the logs, and reclaim the space worktrees use.
- [Run Kraft as a service](/guides/run/run-as-a-service): start Kraft at login, and run a second instance.
- [Upgrade Kraft](/guides/run/upgrade-kraft): back up, upgrade and check an installed Kraft, pin a version, and roll back to an earlier 2.x release.
- [Upgrade your configuration](/guides/run/upgrade-your-configuration): take new shipped chains, library tasks and policy after an upgrade without losing your edits.
- [Upgrade from 1.4](/guides/run/upgrade-from-1-4): what 2.0 changes from 1.4, and the steps to update from it.
- [Upgrade from 0.x or a release candidate](/guides/run/upgrade-from-0-x-or-a-release-candidate): move from a 1.5.0 release candidate, an install from before the `kraft-sdlc` rename, or a 0.x home with the old template configuration.
- [Roll back to 1.4](/guides/run/roll-back): what a rollback does to the database and config, and the steps from 2.0 back to 1.4.

**Without a server.** [Kraft Lite](/guides/kraft-lite): run a chain inside one agent session with no service.
