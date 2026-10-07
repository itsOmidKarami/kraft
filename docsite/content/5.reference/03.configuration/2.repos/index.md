---
title: Repos
navigation:
  title: Overview
description: Every field in repos.yaml, where a repo's value comes from, and the pages for detectors, workspaces and sandboxes.
---

`repos.yaml` lists the repositories you connected and how Kraft works in each. Three groups of pages go deeper: [Detectors](/reference/configuration/repos/detectors) for how `kraft repo connect` fills the entry in, [Workspaces](/reference/configuration/repos/workspaces) for a root repository with members, and [Sandboxed workers](/reference/configuration/sandbox) for the `sandbox` field.

```yaml [config/repos.yaml]
repos:
  - path: /home/you/code/my-service
    name: my-service
    forge: github
    default_chain: default
    enabled: true
    setup_command: "uv sync"
    test_command: null
    intent_dir: null
    env: {}
    env_passthrough: []
    local_files: []
    models: {}
    deny_tools: []
    steering: []
    sandbox: null
    policy:
      allowed_tools: [Read, Edit, Bash]
    automated_review:
      bot: coderabbitai     # exactly one of bot or check
    ci_checks: true
```

## Where a repo's value comes from

A repo that sets no `steering:` gets the steering profiles its default chain's tasks select, from the library. A repo that sets none of `deny_tools`, `models` or a `policy:` key gets the instance's. `deny_tools` and `policy:` come from `policy.yaml`, and a repo's own list only adds to it.

A repo's `policy:` may tighten the instance's safety layer but not relax it. A draft of `repos.yaml` shows a relaxing value as a problem naming the repo and the field before it is published, the same refusal a run gives.

Each field's source (`repo`, `library` or `default`) comes with the value in the `repos` draft's `resolved` view; see the [HTTP API reference](/reference/http-api).

## Fields

No key on an entry passes silently. A key within two edits of a field below (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the tables do not list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

### Identity

| Field | Type | Default | Meaning |
|---|---|---|---|
| `path` | string | *(required)* | Absolute path to the repo. |
| `name` | string | `null` | Display name, set at connect time and not otherwise validated. Unset, the path is shown. |
| `id` | string, `[a-z][a-z0-9_-]*` | `null` | The repository id a workspace names this entry by, unique. Only a workspace's root and members need one; connecting a repo with submodules writes it for them. |
| `enabled` | boolean | `true` | Set `false` to keep auto-intake off this repo without disconnecting it. See [`enabled`](#enabled). |
| `managed` | boolean | `true` | Keeps a human-connected repo out of Settings › Repos' "Detected · not connected" section. Auto-connected submodules are written with `managed: false`. |
| `forge` | string | `null` | `github` or `gitlab`: which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the host of the repo's remote: `github.com`, `gitlab.com`, or a self-hosted host that names one (`gitlab.example.com`). |
| `project` | string | `null` | The GitLab project path, when `forge: gitlab`. |
| `default_chain` | string | `null` | The chain a [work item](/concepts/vocabulary#work-item) on this repo runs when none is named. Unset, it is `default`. See [`default_chain`](#default_chain). |
| `models` | mapping of [harness profile](/concepts/vocabulary#harness-profile) id to string | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`). See [`models`](#models). |

### Test and setup

| Field | Type | Default | Meaning |
|---|---|---|---|
| `setup_command` | string | *(required, no fallback)* | Runs in every new [worktree](/concepts/vocabulary#worktree) before any node starts. See [`setup_command`](#setup_command). |
| `test_command` | string | `null` | The command CI actually runs for this repo: what the changed-test-scope verification runs, as one scope over every path. See [`test_command`](#test_command). |
| `test_scopes` | list of `{paths, command}` | `null` | A monorepo's per-directory test commands. Each `paths` is a non-empty list and each `command` a non-empty string. See [`test_scopes`](#test_scopes). |
| `areas` | mapping of id to area | `{}` | Path-scoped contexts inside this repo: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. See [`areas`](#areas). |
| `intent_dir` | string, a relative path | `null` | Where the repo's intent tree lives, relative to its root. When set, every agent in the repo is told to follow it. Kraft does not run the tree's check: add it to the repo's `test_scopes` yourself. |
| `local_files` | list of strings | `[]` | Relative file paths (no globs, no directories) to copy into every new worktree, for files `git worktree add` cannot carry. See [`local_files`](#local_files). |

### Environment

| Field | Type | Default | Meaning |
|---|---|---|---|
| `env` | mapping of string to string | `{}` | Literal environment variables every worker for this repo gets. See [`env`](#env). |
| `env_passthrough` | list of strings | `[]` | Names of variables to carry over from the server's own environment, for what the worker allowlist does not cover. See [`env`](#env). |

### Policy and sandbox

| Field | Type | Default | Meaning |
|---|---|---|---|
| `policy` | mapping | `null` | The repository policy layer, applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. See [`policy`](#policy). |
| `deny_tools` | list of strings | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer, frozen into each work item when it is filed. See [`policy`](#policy). |
| `steering` | list of strings | `[]` | Names of `library.yaml` steering profiles given to every agent launch on this repo, before the task's own steering. Frozen into each work item when it is filed. See [Repository steering](#repository-steering). |
| `sandbox` | mapping, or `false` | `null` | Runs this repo's task processes in a container. See [`sandbox`](#sandbox). |

### Review and CI

| Field | Type | Default | Meaning |
|---|---|---|---|
| `automated_review` | mapping | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for: `bot: <login>` or `check: <name>`. See [Automated review](#automated-review). |
| `ci_checks` | boolean | `true` | Set `false` on a repo with no CI, so both CI waits pass at once instead of waiting on checks that never come. See [`ci_checks`](#ci_checks). |

## Field details

### `enabled`

- It governs auto-intake only: you can still file and run items on a disabled repo (from the CLI or an agent; the web composer lists only enabled repos), and running items keep going.
- `kraft repo connect` saves a repo it found no test command for with `enabled: false`; an item on it stops at `verify` until it has a `test_command` or `test_scopes`.
- An absent key counts as enabled.
- Kraft refuses an edit that would leave an enabled repo with neither a `test_command` (`""` counts) nor `test_scopes`.

### `default_chain`

An item that names no chain gets it however it is filed: `kraft item create`, the MCP tool, the board, the API, `POST /api/triggers` or auto-intake.

Before 2.0, this key was `default_chain_template`. It is still read, and saved under the new name.

### `models`

It applies above the profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. It is keyed by profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning.

### `test_command`

- It runs from the worktree root without a shell.
- `""` declares a repo with no tests (docs, infrastructure): verification passes and its session says so. It is the **No tests** checkbox in Settings › Repos.
- A repo with neither this nor `test_scopes` stops that verification for you rather than inventing a command.
- Verification ignores it while `test_scopes` is set: the scopes are what runs. For a repo with more than one project, connect saves the first scope's command here, beside the scopes, so it does not cover every path.

[Detectors](/reference/configuration/repos/detectors) says how connect proposes it.

### `areas`

An area's test scopes join the repo's and are selected by changed paths the same way. Its `setup` runs once before the first of its scopes runs. Areas are never forge targets.

### `test_scopes`

Every command runs from the repository root, without a shell, so one for a project in a subdirectory names that directory itself: `sh -c 'cd frontend && npm test'` (what connect proposes) or `npm --prefix frontend test`, not `npm test`.

Kraft does not synthesize `test_scopes` from `test_command`: the two stay independently editable.

### `setup_command`

- `""` means "deliberately nothing", which is the **No setup needed** checkbox in Settings › Repos.
- An absent value stops the repo's next work item rather than guessing.
- On a [sandboxed](/concepts/vocabulary#sandbox) item it runs as `sh -c` inside the sandbox, never on the host. Without docker the item stops.

[Detectors](/reference/configuration/repos/detectors) says how connect proposes it.

### `env`

`env` sets literal variables every worker for this repo gets, on top of the worker's allowlist. `env_passthrough` names variables the server already has that the allowlist leaves out. Both are layered on the allowlist, which is not the server's whole environment: [Passed to workers](/reference/configuration/environment-variables#passed-to-workers) lists it.

- Another agent's key, such as `CODEX_API_KEY`, needs `env_passthrough`.
- Any other `KRAFT_*` variable the server has stays behind: set it in `env`, or name it in `env_passthrough`.
- A sandbox gets each `env_passthrough` name by name, so its value never appears on the `docker` command line.

A sandboxed worker gets none of the allowlist, only what [Sandboxed workers](/reference/configuration/sandbox) lists, which covers the server's proxy and an extra CA.

### `local_files`

Only a file the worktree's `.gitignore` covers is copied. An entry that is not covered, or a directory, is refused and named in its own section of the item's `worktree_prepared` event (`kraft view events`).

### `sandbox`

`sandbox` is part of the repository policy layer. Once it is set, no chain, node or task can turn it off or change it, limits and network policy included, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both.

The keys, and which go with `kind: docker` and which with `kind: kit`, are in [Sandboxed workers](/reference/configuration/sandbox#the-sandbox-key).

### `policy`

It takes any of:

- `allowed_tools`, `deny_tools`, `grants`, `sandbox`, `allowed_harnesses`, `escalation_harness`, `timeout_minutes` and `max_attempts`
- the four caps (`time_cap_minutes`, `total_time_cap_minutes`, `token_budget`, `budget_usd`), each the work item's own, within `maxima.work_item`

`deny_tools` is frozen into each work item when it is filed, and a later addition still applies to running items.

It binds every work item filed in this repo, whatever its chain. A value `policy.yaml` refuses makes [intake](/concepts/vocabulary#intake) refuse the item. See [Policy fields](/reference/configuration/policy#policy-fields).

### `ci_checks`

The two waits are a chain's `mr.ci` task before the merge and its `mr.post_merge_ci` task after it. Each records `ci_not_configured` when it passes at once because `ci_checks` is `false`. It does not touch `mr.automated_review` or `mr.external_approval`.

## Automated review

`automated_review` names exactly one reviewer, one of two ways. It is the reviewer a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has reviewed the merge request's current head.
  - On GitHub, changes requested or any inline comment is actionable (one finding per comment), and anything else is clean. A dismissed review does not count.
  - On GitLab, the bot's unresolved discussions are actionable and its approval is clean. A GitLab approval is not tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push.
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for you rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

## Repository steering

`steering: [name, ...]` names steering profiles in `library.yaml`, the same ones a task's `steering:` selects (see the [library reference](/reference/configuration/library-and-chains)). There is no other steering store.

- When a work item is filed, Kraft resolves each name to its profile's `instructions` and freezes the text into the item. Editing a profile reaches items filed afterwards and never one already filed.
- Every launch gets the repository's profiles first, then the task's, in the agent's system prompt under a `## Project standards` heading, 8 KB at most together.
- A name the library does not define is refused when the repository is saved (Settings › Repos) and when an item is filed. A library save that removes a profile a repository still names is refused too.
- You write profiles in Templates › Library, which edits `library.yaml`.

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`, `AGENTS.md`, or anything else from inside the repo being worked on as a source of process context.

## In this section

- [Detectors](/reference/configuration/repos/detectors): how `kraft repo connect` proposes setup and test commands, what to do when it finds none, and `detectors.yaml`.
- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.

## Related

- [Sandboxed workers](/reference/configuration/sandbox): what reaches a sandboxed worker's container, with the pages for its callbacks, network policy, credentials, Kits and `sandbox.yaml`.
