---
title: Configuration
description: Where Kraft's configuration files live and which file controls what.
---

Kraft's configuration is a set of YAML files under `$KRAFT_HOME/templates/` (default `~/.kraft/templates/`); this page maps each file to its reference page.

Kraft seeds the directory from the packaged defaults on first run and never
overwrites it afterwards, so an upgrade cannot clobber an edited policy. The
Settings screens in the UI edit these same files, and editing them by hand is
equally supported.

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` picks up an on-disk edit without a restart.
- `kraft admin templates lint` checks the whole library and every chain at
  once, and writes nothing.

Kraft validates each file when it loads it, and reports a file that fails with
the offending key named. A chain component may only `extends` a library
component of its own kind, and a `reject_to` may only name a node before the
gate that declares it.

`registry.yaml`, hook names and `gate_after` do not exist in the current
template format (V1). Kraft refuses a home that still holds them until you run
`kraft admin update`. See
[Migrating a pre-V1 template configuration](/reference/cli/admin#migrating-a-pre-v1-template-configuration).

## Files

| File | What it configures | Page |
|---|---|---|
| `repos.yaml` | Connected repos: setup command, env, steering, workspaces. | [Repos](/reference/configuration/repos) |
| `policy.yaml` | Caps, budget, archiving, defaults and maxima, triggers. | [Policy](/reference/configuration/policy) |
| `library.yaml` and `chains/*.yaml` | Reusable components and the chain templates built from them. | [Library and chains](/reference/configuration/library-and-chains) |
| `harnesses.yaml` | Harness profiles and agent profiles. | [Harnesses file](/reference/configuration/harnesses-file) |
| `access.yaml` | Bind address, password, remote access. | [Access](/reference/configuration/access) |
| `intake.yaml` | Autonomous pickup of issues. | [Intake](/reference/configuration/intake) |
| `notify.yaml`, `theme.yaml` | Notification webhook and UI appearance. | [Settings-only files](#settings-only-files) |

## Settings-only files

`notify.yaml` and `theme.yaml` are written by the Settings screens, and Kraft rereads `notify.yaml` after each save. You can edit
them by hand, but nothing else in this section depends on them.

| File | Fields |
|---|---|
| `notify.yaml` | `enabled`; `url`, the webhook Kraft posts to, which is a secret and is never shown back; `base_url`, the address links in a notification use; and `events`, the event types that send one. |
| `theme.yaml` | `palette` (`nocturne`, `rose`, `forest`, `amber` or `slate`; default `nocturne`); `mode` (`light`, `dark` or `system`; default `dark`); `density` (`compact` or `comfortable`; default `compact`); and `board` with `group_by` (`status`, `repo` or `template`), `show_done` (default `5`) and `open_in` (`peek` or `full`). |
