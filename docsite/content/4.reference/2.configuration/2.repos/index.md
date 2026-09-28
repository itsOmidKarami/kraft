---
title: Repos
description: Every field in repos.yaml, and how kraft repo connect fills it in.
---

`repos.yaml` lists the repositories you connected and how Kraft works in each.

```yaml
repos:
  - path: /home/you/code/my-service
    name: my-service
    default_chain_template: default
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
| `enabled` | `true` | Set `false` to take this repo out of service without disconnecting it: new items can't target it and auto-intake skips it, while running items keep going. An absent key counts as enabled. Kraft refuses an edit that would leave an enabled repo with neither a `test_command` nor `test_scopes`. |
| `managed` | `true` | Keeps a human-connected repo out of Settings' "Detected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly. |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the repo's remote. |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. A legacy `gitlab_project` key still reads. |
| `models` | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`): above the profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. Keyed by profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning. |
| `test_command` | `null` | The command CI actually runs for this repo — what the changed-test-scope verification runs, as one scope over every path. A repo with neither this nor `test_scopes` stops that verification for a human rather than inventing a command. |
| `areas` | `{}` | Path-scoped contexts inside this repo, keyed by id: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. An area's test scopes join the repo's and are selected by changed paths the same way; its `setup` runs once before the first of its scopes runs. Areas are never forge targets. |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. Not synthesized from `test_command` — the two stay independently editable. |
| `intent_dir` | `null` | Where the repo's intent tree lives, relative to its root. When set, every agent in the repo is told to follow it. Its check runs as one of the repo's `test_scopes`. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. `""` means "deliberately nothing"; an absent value stops the repo's next work item rather than guessing. On a sandboxed item it runs as `sh -c` inside the sandbox, never on the host; without docker the item stops. |
| `env` | `{}` | Literal environment variables every worker for this repo gets. A worker's environment is an allowlist, not the daemon's: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `TERM`, `TZ`, `TMPDIR`, `SSH_AUTH_SOCK`, the proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, any case), `SSL_CERT_FILE`, Kraft's own `KRAFT_*` variables and the agent's credential variable. These values are layered on top of it. A sandboxed worker gets none of that allowlist, only what [Sandboxed workers](#sandboxed-workers) lists; name a proxy or CA variable in `env_passthrough` if its image needs one. |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the worker allowlist under `env` doesn't cover. A sandbox gets each by name, so its value never appears on the `docker` command line. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. Only a file the worktree's `.gitignore` covers is copied; an entry that is not, or a directory, is refused and named in its own section of the item's `worktree_prepared` event (`kraft view events`). |
| `deny_tools` | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer (see the `policy` row): frozen into each work item when it is filed, and a later addition still applies to running items. |
| `steering` | `[]` | Names of `library.yaml` steering profiles given to every agent launch on this repo, before the task's own steering. Frozen into each work item when it is filed. |
| `sandbox` | `null` | `{kind: docker, image: ...}` — run this repo's task processes in that container. Part of the repository policy layer: once set, no chain, node or task can turn it off, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both. See [Sandboxed workers](#sandboxed-workers). |
| `policy` | `null` | The repository policy layer: any of `allowed_tools`, `deny_tools`, `grants`, `sandbox`, `allowed_harnesses`, `timeout_minutes`, `max_attempts` and the four caps (`time_cap_minutes`, `total_time_cap_minutes`, `token_budget`, `budget_usd`, each the work item's own, within `maxima.work_item`), applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. It binds every work item filed in this repo, whatever its chain; a value `policy.yaml` refuses makes [intake](/concepts/vocabulary#intake) refuse the item. See [Policy fields](/reference/configuration/policy#policy-fields). |
| `automated_review` | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for: `bot: <login>` or `check: <name>`. See [Automated review](#automated-review). |

No key on an entry passes silently. A key within two edits of a field above (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the table doesn't list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

## Sandboxed workers

A sandboxed task runs `docker run --rm --init` as your own uid, with every capability dropped. Only what is listed here reaches the container.

| What | How it reaches the container |
|---|---|
| The worktree | Mounted read-write at its real path. |
| The repository's refs | A private copy per worktree, under `$KRAFT_HOME/run/sandbox-git/`. The worker sees every branch and tag as of its session's start, and may create, move or delete refs, but only there. When a session ends, Kraft moves the item's branch in your repository to where the worker left it, and only if nothing else moved it since; any other ref the worker changed is dropped. What Kraft relies on to decide that lives outside the copy, where the worker cannot write. A branch Kraft could not move is recorded as a `sandbox_branch_not_synced` event, and its commit is kept at `refs/kraft/unsynced/<branch>`. A setup command sees the copy but never moves a branch. |
| Objects and LFS content | Your repository's `objects/` and `lfs/`, read-write: they are data a commit writes. `objects/info` (where alternates are named) and alternates themselves are read-only. |
| `HOME` | `$KRAFT_HOME/run/sandbox-home/<work item>`, read-write and kept across sessions, so an agent CLI keeps its state and can resume a paused session. A CLI config directory Kraft owns (Cursor's) lives there too, one per item. |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` and every name in `env_passthrough`, forwarded by name, so their values never appear on the `docker` command line. Values in `env` are passed literally and are visible to `ps`; keep secrets in `env_passthrough`. Logins kept in your home directory or the OS keychain do not reach a sandbox. |
| Git identity | `GIT_AUTHOR_*` and `GIT_COMMITTER_*` from the daemon's environment, else your `user.name` and `user.email` for the repository. Repository hooks never run in the container, as they never run on a worker's commits outside one. |
| Tool policy | Enforced inside the container or refused. Amp's and OpenCode's rules travel with the launch, and Amp's rules file is mounted read-only. Cursor's and Codex's policy needs Kraft's permission hook, which cannot run in a container, so a sandboxed Cursor or Codex task with `allowed_tools`, `deny_tools` or a grant other than `git-commit` is refused. Codex runs with `sandbox_mode=danger-full-access` unless a task or harness profile sets a mode other than `workspace-write`, because its own sandbox cannot start inside Docker. |

**The image** must hold the agent CLI (and, for a fallback, every harness it may fall back to) on its `PATH`, plus `git`, `sh` and CA certificates. Before a task's first launch in an image, Kraft asks the image, through its own entrypoint and with the repository's `env`, whether the command is there; an image that answers no stops the item as a configuration error before anything runs. `kraft admin doctor` checks that the daemon answers and the image is pulled; pull it before filing work, or the first launch pulls it inside the task's time cap.

**Not yet covered.** The container has the default bridge network: open egress, and on a cloud VM the metadata address is reachable. There are no memory, CPU or process limits. A worker can still delete objects from your repository, which breaks it loudly but cannot put content on another branch. A worker cannot reach Kraft's API, so `kraft item reply`, progress reports and an escalation's self-retry do not work from inside a sandbox. Only repositories keeping refs in git's default files storage are supported; a reftable repository stops the item.

Abandoning or archiving an item removes its ref store and sandbox home.

## Automated review

`automated_review` names exactly one reviewer, one of two ways. It is the reviewer a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has reviewed the merge request's current head. On GitHub, changes requested or any inline comment is actionable (one finding per comment), and anything else is clean. A dismissed review does not count. On GitLab, the bot's unresolved discussions are actionable and its approval is clean. A GitLab approval is not tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push.
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

## Connecting a repo

`kraft repo connect` probes a `setup_command` and a test command from the repo's
markers (a justfile with a `test` recipe proposes `just test` ahead of any
manifest) and prints the test command with the file it came from; check both
before trusting them, and `kraft admin doctor` reports any connected repo still
missing a `setup_command`.

## Repository steering

`repos.yaml`'s `steering: [name, ...]` names steering profiles in
`library.yaml`, the same ones a task's `steering:` selects (see the
[library reference](/reference/configuration/library-and-chains)). There is no other steering store. When a work item is filed,
Kraft resolves each name to its profile's `instructions` and freezes the text
into the item, so editing a profile reaches items filed afterwards and never
one already filed. Every launch gets the repository's profiles first, then
the task's, in the agent's system prompt under a `## Project standards`
heading, 8 KB at most together.

A name the library doesn't define is refused when the repository is saved
(Settings, Repos) and when an item is filed, and a
library save that removes a profile a repository still names is refused too.
You write profiles on Settings, Library, which edits `library.yaml`.

A `templates/steering/*.md` directory from an older release is folded into `library.yaml` as steering profiles of the same name on first start. The old directory is kept as `templates/steering.pre-1.0/`.

This is not a place for target-repo files: Kraft never reads `CLAUDE.md`,
`AGENTS.md`, or anything else from inside the repo being worked on as a
source of process context.

## In this section

- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.
