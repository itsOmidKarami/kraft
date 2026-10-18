---
title: Run the server
navigation:
  title: Overview
description: Reach, back up, upgrade and run the Kraft server.
---

_Added in 2.1_

Each guide takes one task from start to finish.

## In this section

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
- [Run items in your own checkout](/guides/run/run-items-in-place): let a repo's work items run in its own checkout instead of a worktree each, and clear a `checkout` stop.
