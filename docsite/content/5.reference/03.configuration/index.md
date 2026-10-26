---
title: Configuration
navigation:
  title: Overview
description: Which file in Kraft's config directory controls what, and how Kraft creates, edits, validates and rereads the directory.
---

Kraft's configuration is a set of YAML files under `$KRAFT_HOME/config/` (default `~/.kraft/config/`). This page maps each file to its reference page and says how the directory is created and edited.

## Files

| File | What it configures | Created | Page |
|---|---|---|---|
| `repos.yaml` | Connected repos: setup command, env, steering, workspaces, and the `sandbox` field ([Sandboxed workers](/reference/configuration/sandbox)). | Seeded, empty | [repos.yaml](/reference/configuration/repos) |
| `policy.yaml` | Caps, budget, concurrency, archiving, defaults and maxima. | Seeded | [policy.yaml](/reference/configuration/policy) |
| `library.yaml` and `chains/*.yaml` | The templates: reusable components and the chains built from them, copied onto each [work item](/concepts/vocabulary#work-item) at intake. | Seeded: the `default` and `quick-task` chains | [Library and chains](/reference/configuration/library-and-chains) |
| `harnesses.yaml` | [Harness profiles](/concepts/vocabulary#harness-profile) and [agent profiles](/concepts/vocabulary#agent-profile). | Seeded | [harnesses.yaml](/reference/configuration/harnesses-file) |
| `access.yaml` | Bind address, password, remote access. | The first save in Settings | [access.yaml](/reference/configuration/access) |
| `intake.yaml` | Autonomous pickup of issues, and schedules. | Seeded | [intake.yaml](/reference/configuration/intake) |
| `notify.yaml` | The notification webhook. | The first save in Settings | [notify.yaml](/reference/configuration/notify) |
| `update.yaml` | Automatic updates, restarting when Kraft is free, and the hours Kraft may do either in. _Added in 2.3_ | The first save in Settings | [update.yaml](/reference/configuration/update) |
| `workers.yaml` | The machines that may run work item steps, and how each connects. _Added in 2.4_ | `kraft admin worker`, or you | [workers.yaml](/reference/configuration/workers) |
| `machine.yaml` | On a machine, not the server: how many steps that machine runs at once. _Added in 2.4_ | You | [The machine's own limit](/reference/configuration/workers#the-machines-own-limit) |
| `theme.yaml` | UI appearance. | The first save in Settings | [theme.yaml](/reference/configuration/theme) |
| `sandbox.yaml` | Which container CLI runs [sandboxed](/concepts/vocabulary#sandbox) tasks, SELinux, and an extra CA. | You | [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml) |
| `detectors.yaml` | Your own conventions for the setup and test commands `kraft repo connect` proposes, layered on the shipped table. | You | [detectors.yaml](/reference/configuration/repos/detectors) |
| `harnesses/*.yaml` | Your own [harness](/concepts/vocabulary#harness) definitions, which add a harness or override a shipped one. | You | [Harness definition files](/reference/harnesses/harness-files) |
| `addons.yaml` | The [collections](/concepts/vocabulary#collection) this instance knows and the [Kraft add-ons](/concepts/vocabulary#add-on) installed from them. | `kraft admin addon`, or you | [Add-ons and collections](/reference/configuration/addons#addonsyaml) |
| `addons.lock` | The commit, digest and version each installed add-on is pinned to. Not edited by hand. | `kraft admin addon` | [Add-ons and collections](/reference/configuration/addons#addonslock) |
| `repo-libraries.lock` | The commit and digest each connected repository's [repo library](/concepts/vocabulary#repo-library) was accepted at. Not edited by hand. | `kraft repo library` | [Repo libraries](/reference/configuration/repo-libraries#repo-librarieslock) |

## How Kraft treats the directory

- **Seeding.** Kraft copies the shipped defaults into `config/` on first run and never overwrites it afterwards, so an upgrade cannot clobber an edited policy. [Upgrade your configuration](/guides/run/upgrade-your-configuration) takes what a new release ships in `library.yaml` and `chains/`. The files marked "You", "`kraft admin addon`" or "The first save in Settings" are never seeded.
- **Editing.** The **Templates** screens edit `library.yaml` and `chains/`. **Settings** edits the rest, except `sandbox.yaml`, `detectors.yaml`, `harnesses/` and the add-on files and locks, which no screen edits. Editing any file by hand is equally supported.
- **Moving it.** `KRAFT_HOME` and `KRAFT_CONFIG_DIR` move the directory; see [Environment variables](/reference/configuration/environment-variables).
- **Validation.** Kraft validates each file when it loads it, and reports a file that fails with the offending key named. Chain rules such as `extends` and `reject_to` are in [Chain file keys](/reference/chain-nodes).
- **An old directory.** Before 2.0, the directory was `templates/`; see [Changes from 1.4 to 2.0](/reference/changes-from-1-4#config-directory-and-files). A home still in the 0.x template format (a `registry.yaml`, hook names or `gate_after`) is refused until you run `kraft admin update`; see [Migrating a home from 0.x](/guides/run/upgrade-from-0-x-or-a-release-candidate#migrating-a-home-from-0x).

Three commands check and reload the files:

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` rereads `policy.yaml`, `library.yaml` with its chains, `intake.yaml`, `workers.yaml`, and `addons.yaml` with `addons.lock`, without a restart. A change to `access.yaml`'s `bind` or `port` needs a restart, and a hand edit of `notify.yaml` takes effect at the next save in Settings or a restart.
- `kraft admin templates lint` checks the whole library and every chain at once, and writes nothing.

## In this section

- [repos.yaml](/reference/configuration/repos): every field in `repos.yaml`, and the pages for how connect proposes commands, `detectors.yaml` and workspaces.
- [policy.yaml](/reference/configuration/policy): every field in `policy.yaml`: caps, budget, concurrency, archiving, defaults and maxima.
- [Library and chains](/reference/configuration/library-and-chains): the sections of `library.yaml`, and how `chains/*.yaml` files compose them into chains.
- [harnesses.yaml](/reference/configuration/harnesses-file): every field in `harnesses.yaml`, for harness profiles and agent profiles.
- [access.yaml](/reference/configuration/access): every field in `access.yaml`: bind address, port, password, session expiry and allowed hosts.
- [intake.yaml](/reference/configuration/intake): every field in `intake.yaml`: autonomous pickup from beads, and cron schedules.
- [notify.yaml](/reference/configuration/notify): every field in `notify.yaml`: the notification webhook and the events that post to it.
- [theme.yaml](/reference/configuration/theme): every field in `theme.yaml`: colors, density, the diff and the board.
- [Sandboxed workers](/reference/configuration/sandbox): the `sandbox` key of `repos.yaml`, and the pages for the container's resource limits, callbacks, network, credentials, Kits, `sandbox.yaml` and git.
- [Environment variables](/reference/configuration/environment-variables): the variables that move this directory, every other variable Kraft reads, and the variables a worker gets.
- [Add-ons and collections](/reference/configuration/addons): `collection.json`, `addon.json`, `addons.yaml` and `addons.lock`, the naming rules, and when an add-on loads, updates and is restored.
- [Repo libraries](/reference/configuration/repo-libraries): a repository's own `.kraft/` library, where it is read from, how a person accepts it, and `repo-libraries.lock`.
- [update.yaml](/reference/configuration/update): every field in `update.yaml`: automatic updates, restarting when Kraft is free, the channel and the allowed hours.
- [workers.yaml](/reference/configuration/workers): every field in `workers.yaml`: the machines that may run steps, how each connects, and why one is offline.
