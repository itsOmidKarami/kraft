---
title: Configuration
navigation:
  title: Overview
description: Where Kraft's configuration files live and which file controls what.
---

Kraft's configuration is a set of YAML files under `$KRAFT_HOME/config/` (default `~/.kraft/config/`); this page maps each file to its reference page.

Kraft seeds the directory from the packaged defaults on first run and never
overwrites it afterwards, so an upgrade cannot clobber an edited policy. The
Templates and Settings screens in the UI edit these same files, and editing them by hand is
equally supported.

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` picks up an on-disk edit without a restart.
- `kraft admin templates lint` checks the whole library and every chain at
  once, and writes nothing.

`KRAFT_HOME` and `KRAFT_CONFIG_DIR` move this directory. See
[Environment variables](/reference/configuration/environment-variables) for
those and every other variable Kraft reads.

Kraft validates each file when it loads it, and reports a file that fails with
the offending key named. A chain component may only `extends` a library
component of its own kind, and a `reject_to` may only name a node before the
gate that declares it.

`registry.yaml`, hook names and `gate_after` do not exist in the current
template format. Kraft refuses a home that still holds them until you run
`kraft admin update`. See
[Migrating an older template configuration](/reference/cli/admin#migrating-an-older-template-configuration).

## Files

| File | What it configures | Page |
|---|---|---|
| `repos.yaml` | Connected repos: setup command, env, steering, workspaces. | [Repos](/reference/configuration/repos) |
| `policy.yaml` | Caps, budget, archiving, defaults and maxima, triggers. | [Policy](/reference/configuration/policy) |
| `library.yaml` and `chains/*.yaml` | Reusable components and the chain templates built from them. | [Library and chains](/reference/configuration/library-and-chains) |
| `harnesses.yaml` | Harness profiles and agent profiles. | [Harnesses file](/reference/configuration/harnesses-file) |
| `access.yaml` | Bind address, password, remote access. | [Access](/reference/configuration/access) |
| `intake.yaml` | Autonomous pickup of issues. | [Intake](/reference/configuration/intake) |
| `sandbox.yaml` | Which container CLI runs sandboxed tasks, SELinux, and an extra CA. | [Sandbox host](/reference/configuration/sandbox) |
| `notify.yaml`, `theme.yaml` | Notification webhook and UI appearance. | [Settings-only files](#settings-only-files) |

## Settings-only files

`notify.yaml` and `theme.yaml` are written by the Settings screens (Notifications and Appearance), and Kraft rereads `notify.yaml` after each save. You can edit
them by hand, but nothing else in this section depends on them.

| File | Fields |
|---|---|
| `notify.yaml` | `enabled`; `url`, the webhook Kraft posts to, which is a secret and is never shown back; `base_url`, the address links in a notification use; and `events`, the event types that send one. See [Notifications](/reference/events#notifications) for the payload and every event type. |
| `theme.yaml` | The look: `surface`, `accent` and `colour_amount`; `mode` and `density`; `code_scheme`, `diff` and `board`. See [theme.yaml](#themeyaml). |

## theme.yaml

Settings › Appearance writes this file. A save changes only the keys it sends,
and a key the file leaves out takes its default.

| Key | Values | Default |
|---|---|---|
| `surface` | `graphite`, `slate`, `ink`, `sand` or `moss`: the base colours of the page. | `ink` |
| `accent` | `none`, `blue`, `violet`, `green`, `amber` or `rose`. | `violet`; with a `surface` set and no `accent`, `none` |
| `colour_amount` | `mono`, `subtle` or `full`: how much colour the surfaces, the accent and the status colours carry; `mono` leaves only grey. `mono` takes only `accent: none`, and a file that pairs them is refused. | `full`; with a `surface` set and no amount, `subtle` |
| `mode` | `light`, `dark` or `system`. | `dark` |
| `density` | `compact` or `comfortable`: how tightly rows and text are spaced. | `compact` |
| `code_scheme` | `light`: `auto`, `none` or `solarized-light`; `dark`: `auto`, `none`, `solarized-dark`, `monokai` or `dracula`. The syntax colours in review diffs, chosen separately for light and dark. | `auto` for both |
| `diff` | The review page's diff: `layout` (`unified` or `split`), `colours` (`theme`, `safe` or `plain`), and the switches `show_whitespace` (`true`), `word_highlight` (`true`), `wrap_lines` (`false`) and `one_file_at_a_time` (`true`). | as shown |
| `board` | `group_by` (`status`, `repo` or `template`), `show_done` (the Done group's size, at least `1`; `5`) and `open_in` (`peek` or `full`: whether opening an item shows the side panel or the item page; `peek`). | as shown |

### `palette` is legacy

Before 2.0, `palette` (`nocturne`, `rose`, `forest`, `amber` or `slate`) chose
the colours. No interface writes it any more, and Kraft converts it once at
startup, before anything reads the theme:

- `palette` is removed, and `surface`, `accent` and `colour_amount` are written
  to draw the same look. `nocturne` and `rose` become `ink` with a `violet`
  accent, `forest` becomes `moss` with `green`, `amber` becomes `sand` with
  `amber`, and `slate` becomes `slate` with `blue`. The amount of colour is the
  file's own `colour_amount`, else `full`. A file that already names its own
  `surface` just loses the `palette`.
- The original bytes are saved as `theme.yaml.pre-2.0` first. An existing copy
  is never overwritten, and a `theme.yaml.pre-1.5` or `theme.yaml.pre-ux2`
  that a 1.5.0 release candidate saved counts as that copy: no second one is
  written beside it.
- A missing file, a file with no `palette`, and one that does not parse or
  holds a value Kraft does not know are left alone. With no `theme.yaml` at
  all, the look is `ink` with `violet` at `full`.

A `palette` that reappears, from an old browser tab's save or a hand edit, is
read as it was and converted the same way at the next start.
