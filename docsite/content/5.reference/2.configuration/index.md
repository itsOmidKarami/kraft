---
title: Configuration
navigation:
  title: Overview
description: Which file in Kraft's config directory controls what, what the directory holds, and the two files only Settings writes.
---

Kraft's configuration is a set of YAML files under `$KRAFT_HOME/config/` (default `~/.kraft/config/`). This page maps each file to its reference page and says how the directory is created and edited.

## Files

| File | What it configures | Created | Page |
|---|---|---|---|
| `repos.yaml` | Connected repos: setup command, env, steering, workspaces. | Seeded, empty | [Repos](/reference/configuration/repos); its `sandbox` field: [Sandboxed workers](/reference/configuration/sandbox), [Callbacks and resource limits](/reference/configuration/sandbox/callbacks-and-limits), [Network policy](/reference/configuration/sandbox/network-policy), [Credentials](/reference/configuration/sandbox/credentials), [Kits](/reference/configuration/sandbox/kits) |
| `policy.yaml` | Caps, budget, concurrency, archiving, defaults and maxima. | Seeded | [Policy](/reference/configuration/policy) |
| `library.yaml` and `chains/*.yaml` | The templates: reusable components and the chains built from them, copied onto each work item at intake. | Seeded: the `default` and `quick-task` chains | [Library and chains](/reference/configuration/library-and-chains) |
| `harnesses.yaml` | Harness profiles and agent profiles. | Seeded | [Harnesses file](/reference/configuration/harnesses-file) |
| `access.yaml` | Bind address, password, remote access. | The first save in Settings | [Access](/reference/configuration/access) |
| `intake.yaml` | Autonomous pickup of issues, and schedules. | Seeded | [Intake](/reference/configuration/intake) |
| `sandbox.yaml` | Which container CLI runs sandboxed tasks, SELinux, and an extra CA. | You | [Sandbox host](/reference/configuration/sandbox/sandbox-yaml) |
| `detectors.yaml` | Your own conventions for the setup and test commands `kraft repo connect` proposes, layered on the shipped table. | You | [Detectors](/reference/configuration/repos/detectors) |
| `notify.yaml` | The notification webhook. | The first save in Settings | [notify.yaml](#notifyyaml) |
| `theme.yaml` | UI appearance. | The first save in Settings | [theme.yaml](#themeyaml) |
| `harnesses/*.yaml` | Your own harness definitions, which add a harness or override a shipped one. | You | [Harness files](/reference/harnesses/harness-files) |

## How Kraft treats the directory

- **Seeding.** Kraft copies the shipped defaults into `config/` on first run and never overwrites it afterwards, so an upgrade cannot clobber an edited policy. The files marked "You" or "The first save in Settings" are never seeded.
- **Editing.** The **Templates** screens edit `library.yaml` and `chains/`. **Settings** edits the rest, except `sandbox.yaml`, `detectors.yaml` and `harnesses/`, which no screen edits. Editing any file by hand is equally supported.
- **Moving it.** `KRAFT_HOME` and `KRAFT_CONFIG_DIR` move the directory; see [Environment variables](/reference/configuration/environment-variables).
- **Validation.** Kraft validates each file when it loads it, and reports a file that fails with the offending key named. Chain rules such as `extends` and `reject_to` are in [Chain nodes](/reference/chain-nodes).
- **An old directory.** Before 2.0, the directory was `templates/`; see [Upgrading from 1.4](/guides/run/upgrade-kraft#upgrading-from-14). A home still in the 0.x template format (a `registry.yaml`, hook names or `gate_after`) is refused until you run `kraft admin update`; see [Migrating an older template configuration](/reference/cli/admin#migrating-an-older-template-configuration).

Three commands check and reload the files:

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` rereads `policy.yaml`, `library.yaml` with its chains, and `intake.yaml` without a restart. A change to `access.yaml`'s `bind` or `port` needs a restart, and a hand edit of `notify.yaml` takes effect at the next save in Settings or a restart.
- `kraft admin templates lint` checks the whole library and every chain at once, and writes nothing.

## notify.yaml

Settings › Notifications writes this file, and Kraft rereads it after each save. See [Notifications](/reference/events#notifications) for the payload and every event type.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | boolean | `false` | Whether Kraft posts notifications. |
| `url` | string | unset | The webhook Kraft posts to. It is a secret and is never shown back. |
| `base_url` | string | unset | The address the links in a notification use. |
| `events` | list | `[gate_requested, work_item_needs_human]` | The event types that send a notification. |

## theme.yaml

Settings › Appearance writes this file. A save changes only the keys it sends, and a key the file leaves out takes its default. When a file sets `surface`, a missing `accent` is `none` and a missing `colour_amount` is `subtle`. A file that sets `colour_amount: mono` must leave `accent` out or set it to `none`; Kraft refuses a file that pairs `mono` with another accent.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `surface` | `graphite`, `slate`, `ink`, `sand` or `moss` | `ink` | The base colors of the page. |
| `accent` | `none`, `blue`, `violet`, `green`, `amber` or `rose` | `violet` | The accent color. |
| `colour_amount` | `mono`, `subtle` or `full` | `full` | How much color the surfaces, the accent and the status colors carry. `mono` leaves only grey. |
| `mode` | `light`, `dark` or `system` | `dark` | The color mode. |
| `density` | `compact` or `comfortable` | `compact` | How tightly rows and text are spaced. |
| `code_scheme.light` | `auto`, `none` or `solarized-light` | `auto` | The syntax colors in review diffs in light mode. |
| `code_scheme.dark` | `auto`, `none`, `solarized-dark`, `monokai` or `dracula` | `auto` | The syntax colors in review diffs in dark mode. |
| `diff.layout` | `unified` or `split` | `unified` | The layout of the review page's diff. |
| `diff.colours` | `theme`, `safe` or `plain` | `theme` | The colors of the review page's diff: status colors (`theme`), colorblind-safe blue and orange (`safe`), or marks only (`plain`). |
| `diff.show_whitespace` | boolean | `true` | Show whitespace changes. `false` hides lines that differ only in whitespace. |
| `diff.word_highlight` | boolean | `true` | Highlight changed words: a stronger tint on the words that changed inside a line. |
| `diff.wrap_lines` | boolean | `false` | Wrap long lines. `false` scrolls sideways. |
| `diff.one_file_at_a_time` | boolean | `true` | Show one file at a time, with the file tree beside it. |
| `board.group_by` | `status`, `repo` or `chain` | `status` | What the board groups items by. Before 2.0, `template` meant `chain`. It is still read as `chain`. |
| `board.show_done` | integer, at least `1` | `5` | The size of the Done group. |
| `board.open_in` | `peek` or `full` | `peek` | Whether opening an item shows the side panel (`peek`) or the item page (`full`). |
| `editor` | `code`, `cursor`, `zed` or `obsidian` | unset | The editor a document's **Open in editor** uses. Left out, it is `KRAFT_EDITOR`, else the system's default app. |

### `palette` is legacy

A `palette` key in an old file (`nocturne`, `rose`, `forest`, `amber` or `slate`) is converted once at startup into the three keys that draw the same look, and Kraft saves the original as `theme.yaml.pre-2.0`. A `palette` that reappears, from an old browser tab's save or a hand edit, is read as it was and converted the same way at the next start. The conversion, with `colour_amount` set to the file's own value, else `full`:

| `palette` | `surface` | `accent` |
|---|---|---|
| `nocturne`, `rose` | `ink` | `violet` |
| `forest` | `moss` | `green` |
| `amber` | `sand` | `amber` |
| `slate` | `slate` | `blue` |

A file that already sets its own `surface` only loses the `palette`. A file that does not parse or holds a value Kraft does not know is left alone. The backup names and how to restore one are in [Upgrading from 1.4](/guides/run/upgrade-kraft#where-did-a-page-go-and-why-does-my-theme-look-different).

## Related

- [Environment variables](/reference/configuration/environment-variables): the variables that move this directory, and every other variable Kraft reads.
- [Upgrade your templates](/guides/customize/upgrading-templates): take what a new release ships in `library.yaml` and `chains/`.
