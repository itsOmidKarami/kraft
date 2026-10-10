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
| `theme.yaml` | UI appearance. | The first save in Settings | [theme.yaml](/reference/configuration/theme) |
| `sandbox.yaml` | Which container CLI runs [sandboxed](/concepts/vocabulary#sandbox) tasks, SELinux, and an extra CA. | You | [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml) |
| `detectors.yaml` | Your own conventions for the setup and test commands `kraft repo connect` proposes, layered on the shipped table. | You | [detectors.yaml](/reference/configuration/repos/detectors) |
| `harnesses/*.yaml` | Your own [harness](/concepts/vocabulary#harness) definitions, which add a harness or override a shipped one. | You | [Harness definition files](/reference/harnesses/harness-files) |
| `plugins.yaml` | The [collections](/concepts/vocabulary#collection) this instance knows and the [Kraft plugins](/concepts/vocabulary#kraft-plugin) installed from them. | `kraft admin plugin`, or you | [Plugins and collections](/reference/configuration/plugins#pluginsyaml) |
| `plugins.lock` | The commit, digest and version each installed plugin is pinned to. Not edited by hand. | `kraft admin plugin` | [Plugins and collections](/reference/configuration/plugins#pluginslock) |

## How Kraft treats the directory

- **Seeding.** Kraft copies the shipped defaults into `config/` on first run and never overwrites it afterwards, so an upgrade cannot clobber an edited policy. [Upgrade your configuration](/guides/run/upgrade-your-configuration) takes what a new release ships in `library.yaml` and `chains/`. The files marked "You", "`kraft admin plugin`" or "The first save in Settings" are never seeded.
- **Editing.** The **Templates** screens edit `library.yaml` and `chains/`. **Settings** edits the rest, except `sandbox.yaml`, `detectors.yaml`, `harnesses/` and the two plugin files, which no screen edits. Editing any file by hand is equally supported.
- **Moving it.** `KRAFT_HOME` and `KRAFT_CONFIG_DIR` move the directory; see [Environment variables](/reference/configuration/environment-variables).
- **Validation.** Kraft validates each file when it loads it, and reports a file that fails with the offending key named. Chain rules such as `extends` and `reject_to` are in [Chain file keys](/reference/chain-nodes).
- **An old directory.** Before 2.0, the directory was `templates/`; see [Upgrade from 1.4](/guides/run/upgrade-from-1-4#config-directory-and-files). A home still in the 0.x template format (a `registry.yaml`, hook names or `gate_after`) is refused until you run `kraft admin update`; see [Migrating a home from 0.x](/guides/run/upgrade-from-0-x-or-a-release-candidate#migrating-a-home-from-0x).

Three commands check and reload the files:

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` rereads `policy.yaml`, `library.yaml` with its chains, `intake.yaml`, and `plugins.yaml` with `plugins.lock`, without a restart. A change to `access.yaml`'s `bind` or `port` needs a restart, and a hand edit of `notify.yaml` takes effect at the next save in Settings or a restart.
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
- [Plugins and collections](/reference/configuration/plugins): `collection.json`, `plugin.json`, `plugins.yaml` and `plugins.lock`, the naming rules, and when a plugin loads, updates and is restored.
