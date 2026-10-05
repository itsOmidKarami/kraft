---
title: Repos
navigation:
  title: Overview
description: Every field in repos.yaml, and how kraft repo connect fills it in.
---

`repos.yaml` lists the repositories you connected and how Kraft works in each.

```yaml
repos:
  - path: /home/you/code/my-service
    name: my-service
    default_chain: default
    forge: github
    test_command: null
    intent_dir: null
    setup_command: "uv sync"
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
      bot: coderabbitai     # or `check: <name>` -- exactly one of the two
```

| Field | Default | Means |
|---|---|---|
| `path` | *(required)* | Absolute path to the repo. |
| `name` | — | Display name; set at connect time, not otherwise validated. |
| `id` | — | The repository id a workspace names this entry by (`[a-z][a-z0-9_-]*`, unique). Only a workspace's root and members need one; connecting a repo with submodules writes it for them. |
| `enabled` | `true` | Set `false` to keep auto-intake off this repo without disconnecting it. See [`enabled`](#enabled). |
| `managed` | `true` | Keeps a human-connected repo out of Settings › Repos' "Detected · not connected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain` | — | Which chain a work item on this repo runs when none is named explicitly. Unset, it is `default`. See [`default_chain`](#default_chain). |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the host of the repo's remote: `github.com`, `gitlab.com`, or a self-hosted host that names one (`gitlab.example.com`). |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. |
| `models` | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`). See [`models`](#models). |
| `test_command` | `null` | The command CI actually runs for this repo — what the changed-test-scope verification runs, as one scope over every path. See [`test_command`](#test_command). |
| `areas` | `{}` | Path-scoped contexts inside this repo, keyed by id: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. See [`areas`](#areas). |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. See [`test_scopes`](#test_scopes). |
| `intent_dir` | `null` | Where the repo's intent tree lives, relative to its root. When set, every agent in the repo is told to follow it. Kraft does not run the tree's check: add it to the repo's `test_scopes` yourself. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. See [`setup_command`](#setup_command). |
| `env` | `{}` | Literal environment variables every worker for this repo gets. See [`env`](#env). |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the worker allowlist under `env` doesn't cover. A sandbox gets each by name, so its value never appears on the `docker` command line. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. See [`local_files`](#local_files). |
| `deny_tools` | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer (see [`policy`](#policy)): frozen into each work item when it is filed, and a later addition still applies to running items. |
| `steering` | `[]` | Names of `library.yaml` steering profiles given to every agent launch on this repo, before the task's own steering. Frozen into each work item when it is filed. |
| `sandbox` | `null` | `{kind: docker, image: ..., resources: {...}, network: {...}}` — run this repo's task processes in that container, within the optional [resource limits](/reference/configuration/sandbox/callbacks-and-limits#resource-limits) and [network policy](/reference/configuration/sandbox/network-policy). See [`sandbox`](#sandbox). |
| `policy` | `null` | The repository policy layer, applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. See [`policy`](#policy). |
| `automated_review` | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for: `bot: <login>` or `check: <name>`. See [Automated review](#automated-review). |
| `ci_checks` | `true` | Set `false` on a repo with no CI, so both CI waits pass at once instead of waiting on checks that never come. See [`ci_checks`](#ci_checks). |

No key on an entry passes silently. A key within two edits of a field above (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the table doesn't list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

## Field details

### `enabled`

- It governs auto-intake only: you can still file and run items on a disabled repo (from the CLI or an agent; the web composer lists only enabled repos), and running items keep going.
- `kraft repo connect` saves a repo it found no test command for with `enabled: false`; an item on it stops at `verify` until it has a `test_command` or `test_scopes`.
- An absent key counts as enabled.
- Kraft refuses an edit that would leave an enabled repo with neither a `test_command` (`""` counts) nor `test_scopes`.

### `default_chain`

An item that names no chain gets it however it is filed: `kraft item create`, the MCP tool, the board, the API, `POST /api/triggers` or auto-intake.

`default_chain_template` before 2.0: still read, and saved under the new name.

### `models`

It applies above the profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. Keyed by profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning.

### `test_command`

- It runs from the worktree root without a shell.
- `""` declares a repo with no tests (docs, infrastructure): verification passes and its session says so. It is the **No tests** checkbox in Settings › Repos.
- A repo with neither this nor `test_scopes` stops that verification for a human rather than inventing a command.
- Verification ignores it while `test_scopes` is set: the scopes are what runs. For a repo with more than one project, connect saves the first scope's command here, beside the scopes, so it does not cover every path.

### `areas`

An area's test scopes join the repo's and are selected by changed paths the same way; its `setup` runs once before the first of its scopes runs. Areas are never forge targets.

### `test_scopes`

Every command runs from the repository root, without a shell, so one for a project in a subdirectory names that directory itself: `sh -c 'cd frontend && npm test'` (what connect proposes) or `npm --prefix frontend test`, not `npm test`.

Not synthesized from `test_command` — the two stay independently editable.

### `setup_command`

- `""` means "deliberately nothing", which is the **No setup needed** checkbox in Settings › Repos.
- An absent value stops the repo's next work item rather than guessing.
- On a sandboxed item it runs as `sh -c` inside the sandbox, never on the host; without docker the item stops.

### `env`

A worker's environment is an allowlist, not the daemon's:

- `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `TERM`, `TZ`, `TMPDIR`, `SSH_AUTH_SOCK`
- the proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, any case)
- the CA variables (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`)
- nine `KRAFT_*` variables that locate the instance (`KRAFT_HOME`, `KRAFT_RUN_DIR`, `KRAFT_CONFIG_DIR`, its 1.x name `KRAFT_TEMPLATES_DIR`, `KRAFT_SKILLS_DIR`, `KRAFT_HOST`, `KRAFT_PORT`, `KRAFT_DAEMON_PID`, `KRAFT_DAEMON_PORT`)
- the ones Kraft sets for the session itself ([Passed to workers](/reference/configuration/environment-variables#passed-to-workers))
- Claude Code's credential variables (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`)

Another agent's key, such as `CODEX_API_KEY`, needs `env_passthrough`. Any other `KRAFT_*` variable the daemon has stays behind: set it in `env`, or name it in `env_passthrough`. These values are layered on top of it.

A sandboxed worker gets none of that allowlist, only what [Sandboxed workers](/reference/configuration/sandbox) lists, which covers the daemon's proxy and an extra CA.

### `local_files`

Only a file the worktree's `.gitignore` covers is copied; an entry that is not, or a directory, is refused and named in its own section of the item's `worktree_prepared` event (`kraft view events`).

### `sandbox`

Part of the repository policy layer: once set, no chain, node or task can turn it off or change it, limits and network policy included, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both. See [Sandboxed workers](/reference/configuration/sandbox).

### `policy`

It takes any of:

- `allowed_tools`, `deny_tools`, `grants`, `sandbox`, `allowed_harnesses`, `escalation_harness`, `timeout_minutes` and `max_attempts`
- the four caps (`time_cap_minutes`, `total_time_cap_minutes`, `token_budget`, `budget_usd`), each the work item's own, within `maxima.work_item`

It binds every work item filed in this repo, whatever its chain; a value `policy.yaml` refuses makes [intake](/concepts/vocabulary#intake) refuse the item. See [Policy fields](/reference/configuration/policy#policy-fields).

### `ci_checks`

The two waits are a chain's `mr.ci` task before the merge and its `mr.post_merge_ci` task after it. Each records `ci_not_configured` when it passes at once because `ci_checks` is `false`. It does not touch `mr.automated_review` or `mr.external_approval`.

## Sandboxed workers

Sandboxed workers have a group of pages of their own: [what reaches the container](/reference/configuration/sandbox), [callbacks and resource limits](/reference/configuration/sandbox/callbacks-and-limits), [network policy](/reference/configuration/sandbox/network-policy), [credentials](/reference/configuration/sandbox/credentials), [Kits](/reference/configuration/sandbox/kits) and the host-wide [sandbox.yaml](/reference/configuration/sandbox/sandbox-yaml).

## Automated review

`automated_review` names exactly one reviewer, one of two ways. It is the reviewer a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has reviewed the merge request's current head. On GitHub, changes requested or any inline comment is actionable (one finding per comment), and anything else is clean. A dismissed review does not count. On GitLab, the bot's unresolved discussions are actionable and its approval is clean. A GitLab approval is not tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push.
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

## Repository steering

`repos.yaml`'s `steering: [name, ...]` names steering profiles in
`library.yaml`, the same ones a task's `steering:` selects (see the
[library reference](/reference/configuration/library-and-chains)). There is no other steering store.

When a work item is filed,
Kraft resolves each name to its profile's `instructions` and freezes the text
into the item, so editing a profile reaches items filed afterwards and never
one already filed.

Every launch gets the repository's profiles first, then
the task's, in the agent's system prompt under a `## Project standards`
heading, 8 KB at most together.

A name the library doesn't define is refused when the repository is saved
(Settings › Repos) and when an item is filed, and a
library save that removes a profile a repository still names is refused too.
You write profiles in Templates › Library, which edits `library.yaml`.

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`,
`AGENTS.md`, or anything else from inside the repo being worked on as a
source of process context.

## Where a repo's value comes from

A repo that sets no `steering:` gets the steering profiles its default chain's
tasks select, from the library. One that sets none of `deny_tools` or `models`
or a `policy:` key gets the instance's: `deny_tools` and `policy:` come from
`policy.yaml`, and a repo's own list only adds to it.

A repo's `policy:` may
tighten the instance's safety layer but not relax it; a draft of `repos.yaml`
shows that as a problem naming the repo and the field before it is published,
the same refusal a run gives.

Each field's source (`repo`, `library` or
`default`) comes with the value in the `repos` draft's `resolved` view; see the
[HTTP API reference](/reference/http-api).

## In this section

- [Detectors](/reference/configuration/repos/detectors): how `kraft repo connect` proposes setup and test commands, and `detectors.yaml`. It also holds [Connecting a repo](/reference/configuration/repos/detectors#connecting-a-repo): checking a proposal, and what to do when connect finds none.
- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.
