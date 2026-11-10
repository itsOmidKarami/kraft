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
- [Restart Kraft without ending running steps](/guides/run/restart-kraft): stop, restart or update the server while its local steps keep running, and what still ends a step.
- [Back up and restore the database](/guides/run/back-up-and-restore): copy the database while the server runs, restore it, use the copy Kraft makes before an upgrade, and roll back to an earlier 2.x release.
- [Logs and disk space](/guides/run/logs-and-disk-space): find the logs, and reclaim the space worktrees use.
- [Run Kraft as a service](/guides/run/run-as-a-service): start Kraft at login, and run a second instance.
- [Upgrade Kraft](/guides/run/upgrade-kraft): back up, upgrade and check an installed Kraft, and pin a version.
- [Move onto kraft-default](/guides/run/upgrade-your-configuration): replace the full copies of Kraft's chains in an older home with chains that extend the built-in, keeping your edits.
- [Coming from 1.4](/guides/run/coming-from-1-4): update from 1.4, act on the changes that need it, go back to 1.4, and move from a release candidate or a 0.x home.
- [Run items in your own checkout](/guides/run/run-items-in-place): let a repo's work items run in its own checkout instead of a worktree each, and clear a `checkout` stop.
- [Run a step on another machine](/guides/run/run-a-step-on-another-machine): register a machine, place a node on it, override the placement for one item, and see what comes back.
