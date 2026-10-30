---
title: repos.yaml
navigation:
  title: Overview
description: Every field in repos.yaml, where a repo's value comes from, and the pages for how connect proposes commands, detectors, workspaces and sandboxes.
---

`repos.yaml` lists the repositories you connected and how Kraft works in each. Four groups of pages go deeper: [How connect proposes commands](/reference/configuration/repos/connect-proposals) for how `kraft repo connect` fills the entry in, [detectors.yaml](/reference/configuration/repos/detectors) for teaching it your own conventions, [Workspaces](/reference/configuration/repos/workspaces) for a root repository with members, and [Sandboxed workers](/reference/configuration/sandbox) for the `sandbox` field.

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
      bot: coderabbitai     # one reviewer: exactly one of bot or check; or a list of them
    ci_checks: true
```

## Where a repo's value comes from

A repo that sets no `steering:` gets the [steering profiles](/concepts/vocabulary#steering-profile) its default chain's tasks select, from the library. A repo that sets none of `deny_tools`, `models` or a `policy:` key gets the instance's. `deny_tools` and `policy:` come from `policy.yaml`, and a repo's own list only adds to it.

A repo's `policy:` may tighten the instance's safety layer but not relax it. A draft of `repos.yaml`, the Settings edit buffer, shows a relaxing value as a problem naming the repo and the field before it is published, the same refusal a run gives.

Each field's source (`repo`, `library` or `default`) comes with the value in the `resolved` view of the `repos` draft; see [The `resolved` answer for repos](/reference/http-api/draft-ops#the-resolved-answer-for-repos).

## Fields

No key on an entry passes silently. A key within two edits of a field below (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the tables do not list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed.

### Identity

| Field | Type | Default | Meaning |
|---|---|---|---|
| `path` | string | *(required)* | Absolute path to the repo. |
| `name` | string | `null` | Display name, set at connect time and not otherwise validated. Unset, the path is shown. |
| `id` | string, `[a-z][a-z0-9_-]*` | `null` | The repository id a workspace names this entry by, unique. Only a workspace's root and members need one. Connecting a repo with submodules, or accepting a repo library, writes it, never `kraft` or an add-on's namespace: `release` becomes `release-2`. |
| `enabled` | boolean | `true` | Set `false` to keep auto-intake off this repo without disconnecting it. See [`enabled`](#enabled). |
| `managed` | boolean | `true` | Keeps a repo you connected out of Settings › Repos' "Detected · not connected" section. Auto-connected submodules are written with `managed: false`. |
| `forge` | string | `null` | `github` or `gitlab`: which forge adapter `backend: auto` resolves to for this repo. `kraft repo connect` sets it from the host of the repo's remote: `github.com`, `gitlab.com`, or a self-hosted host that names one (`gitlab.example.com`). |
| `project` | string | `null` | The GitLab project path, when `forge: gitlab`. |
| `default_chain` | string | `null` | The chain a [work item](/concepts/vocabulary#work-item) on this repo runs when none is named. Unset, it is the repo library's `default` chain when one is loaded, else `default`. See [`default_chain`](#default_chain). |
| `models` | mapping of [harness profile](/concepts/vocabulary#harness-profile) id to string | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude: opus`). See [`models`](#models). |

### Test and setup

| Field | Type | Default | Meaning |
|---|---|---|---|
| `setup_command` | string | `null` | Runs in every new [worktree](/concepts/vocabulary#worktree) before any node starts. Unset, the file still loads, but the repo's next work item stops before its first task. See [`setup_command`](#setup_command). |
| `test_command` | string | `null` | The command CI actually runs for this repo: what the changed-test-scope verification runs, as one scope over every path. See [`test_command`](#test_command). |
| `test_scopes` | list of `{paths, command, targeted_command}` | `null` | A monorepo's per-directory test commands. Each `paths` is a non-empty list and each `command` a non-empty string. `targeted_command` is optional; see [`targeted_command`](#targeted_command). _Added in 2.4_ |
| `areas` | mapping of id to area | `{}` | Path-scoped contexts inside this repo: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. See [`areas`](#areas). |
| `intent_dir` | string, a relative path | `null` | Where the repo's intent tree lives, relative to its root. When set, every agent in the repo is told to follow it. Kraft does not run the tree's check: add it to the repo's `test_scopes` yourself. |
| `local_files` | list of strings | `[]` | Files to copy into every new worktree, for ones `git worktree add` cannot carry: relative paths, no globs or directories, covered by the worktree's `.gitignore`. Anything else is refused, and named in the `worktree_prepared` event (`kraft view events`). |
| `carry` | list of strings | `[]` | Files a test-selection tool keeps its state in, such as `.testmondata`. Kraft seeds them into each new worktree and saves them back after a passing local targeted round. See [`carry`](#carry). _Added in 2.4_ |

### Environment

| Field | Type | Default | Meaning |
|---|---|---|---|
| `env` | mapping of string to string | `{}` | Literal environment variables every [worker](/concepts/vocabulary#worker) for this repo gets. See [`env`](#env). |
| `env_passthrough` | list of strings | `[]` | Names of variables to carry over from the server's own environment, for what the worker allowlist does not cover. See [`env`](#env). |

### Policy and sandbox

| Field | Type | Default | Meaning |
|---|---|---|---|
| `policy` | mapping | `null` | The repository policy layer, applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. See [`policy`](#policy). |
| `deny_tools` | list of strings | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer, frozen into each work item when it is filed. See [`policy`](#policy). |
| `steering` | list of strings | `[]` | Names of `library.yaml` steering profiles given to every agent launch on this repo, before the task's own steering. Frozen into each work item when it is filed. See [Repository steering](#repository-steering). |
| `sandbox` | mapping, or `false` | `null` | Runs this repo's task processes in a container, or under the host's own process sandbox (`kind: os`). See [`sandbox`](#sandbox). |
| `worktrees` | boolean | `true` | `false` runs this repo's items in place, in its own checkout, rather than in a worktree each. Refused beside a `sandbox`. See [`worktrees`](#worktrees). _Added in 2.1_ |

### Review and CI

| Field | Type | Default | Meaning |
|---|---|---|---|
| `automated_review` | mapping or list | `null` | The automated reviewers a chain's `mr.automated_review` task waits for, each `bot: <login>` or `check: <name>`. A list names several; `[]` names none. See [Automated review](#automated-review). _Added in 2.4_ |
| `ci_checks` | boolean | `true` | Set `false` on a repo with no CI, so both CI waits pass at once instead of waiting on checks that never come. See [`ci_checks`](#ci_checks). |
| `library_auto_update` | boolean | `false` | Take text-only changes to this repo's accepted [repo library](/reference/configuration/repo-libraries#automatic-updates) without a person. |

## Field details

### `enabled`

- It governs auto-intake only: you can still file and run items on a disabled repo (from the CLI or an agent; the web composer lists only enabled repos), and running items keep going.
- `kraft repo connect` saves a repo it found no test command for with `enabled: false`; an item on it runs its agent tasks, then stops at `verification` until it has a `test_command` or `test_scopes`.
- An absent key counts as enabled.
- Kraft refuses an edit that would leave an enabled repo with neither a `test_command` (`""` counts) nor `test_scopes`.

### `default_chain`

An item that names no chain gets it however it is filed: `kraft item create`, the MCP tool, the board, the API, `POST /api/triggers` or auto-intake.

Unset or `default`, it gives way to the chain `<id>:default` of the repo's [repo library](/reference/configuration/repo-libraries#the-default-chain), when the library is accepted and has one. Name another chain here to keep the instance's own. Accepting a repo library writes an `id` on the entry when it has none.

Before 2.0, this key was `default_chain_template`. It is still read, and saved under the new name.

### `models`

It applies above the harness profile's own `defaults:`, below a task's `model:` or agent `profile:` and the work item's override. It is keyed by harness profile because one model name means nothing to another provider. The retired `default_model` key is dropped with a warning.

### `setup_command`

- `""` means "deliberately nothing", which is the **No setup needed** switch in Settings › Repos.
- An absent value stops the repo's next work item rather than guessing.
- On a [sandboxed](/concepts/vocabulary#sandbox) item it runs as `sh -c` inside the sandbox, never on the host. Without docker the item stops.

[How connect proposes commands](/reference/configuration/repos/connect-proposals) says how connect proposes it.

### `test_command`

- It runs from the worktree root without a shell.
- `""` declares a repo with no tests (docs, infrastructure): verification passes and its session says so. It is the **No tests** switch in Settings › Repos.
- A repo with neither this nor `test_scopes` stops that verification for you rather than inventing a command.
- Verification ignores it while `test_scopes` is set: the scopes are what runs. For a repo with more than one project, connect saves the first scope's command here, beside the scopes, so it does not cover every path.

[How connect proposes commands](/reference/configuration/repos/connect-proposals) says how connect proposes it.

### `test_scopes`

Every command runs from the repository root, without a shell, so one for a project in a subdirectory names that directory itself: `sh -c 'cd frontend && npm test'` (what connect proposes) or `npm --prefix frontend test`, not `npm test`. A command whose own word is a shell operator (`&&`, `|`, `;`, `>`) is refused, and named, when it would run, rather than run with the operator as an argument and the rest ignored.

Kraft does not synthesize `test_scopes` from `test_command`: the two stay independently editable.

#### `targeted_command`
_Added in 2.4_

A scope may name a second command that runs only the tests a change affects. It is optional, and an area's scopes (`areas.<name>.verification.test_scopes`) take it too. Verification runs it in place of `command`, in its first round and in every repair round. A bare `test_command` takes none: write a scope.

- **What it receives.** `KRAFT_TEST_BASE` is the item's whole-branch base commit. `KRAFT_CHANGED_FILES` is the path of a file listing the changed paths, one per line. The list is the diff this round's scopes were selected from: the whole branch in round 0 and after a failed round, and only what changed since after a clean one. Both are set for this command alone, and a sandbox gets the list read-only.
- **An empty list is an answer.** A round that changed nothing since a clean one still runs it, with an empty list. With no list at all, Kraft runs `command` instead: the item has no base commit, or git could not diff it. A workspace member has no list either. The event says why (`full_reason`).
- **What your command must do.** For a change it cannot map to tests, it runs more tests, never fewer: a tool that selects tests from imports has to run everything for a YAML file it knows nothing about. Kraft only hands it the change.
- **The full suite is the chain's.** A chain whose merge request runs CI leaves the full suite to it. A chain without CI adds a node with `suite: full`: see [Run the full suite as its own node](/guides/customize/write-your-own-chain#run-the-full-suite-as-its-own-node).

`kraft repo connect --verify` runs it too, against `HEAD^1` and the last commit's paths, and skips it on a repo with one commit.

Loading refuses an entry where a command string could name two scopes, because sessions are told apart by their command:

- an empty or blank `targeted_command`, or one that is the scope's own `command`;
- two scopes with the same `command` but different `targeted_command`;
- a `targeted_command` equal to another scope's `command`, to another scope's `targeted_command`, or to an area's `setup`.

Commands compare after shell splitting, so `just  test` and `just test` are one string.

### `areas`

An area's test scopes join the repo's and are selected by changed paths the same way. Its `setup` runs once before the first of its scopes runs. Areas are never forge targets.

### `carry`
_Added in 2.4_

Files a test-selection tool keeps its state in, such as testmon's `.testmondata`. They follow `local_files`' rules: literal file paths inside the repo, no globs or directories, covered by the worktree's `.gitignore`, and never listed in both. Kraft seeds them into every new worktree and saves them back, so the tool does not start cold in each one.

- **Seeding.** Kraft copies the whole set from one source: the repo's newest saved copy when it holds any listed file, else the repo's checkout, never a mix. The checkout is only read, and the copy is discarded when a file changes while it is copied, so the tool starts cold. With neither, it starts cold.
- **Saving.** After a verification round of `suite: targeted` in which every scope passed on this machine, and a listed file changed, Kraft saves the worktree's set as the newest copy. A sandboxed item, a remote machine, an in-place item and a workspace member never save.
- **What it is for.** Only state that is safe when stale: the tool checks file content, runs more when unsure, and updates on every run. A tool that trusts its cache by timestamp alone does not belong here.
- **Throwing one away.** `run/carried/<repo>-<hash>/current` points at the newest copy, under `$KRAFT_RUN_DIR`. Delete that repo's folder and the next item seeds from the checkout, or starts cold. `kraft view storage` counts the copies under "other", outside the quota.
- **Sandboxed repos.** A repo whose items always run sandboxed never saves, so it stays as warm as its checkout's own record.

`kraft repo connect --verify` seeds the same set and lists where each file came from.

### `env`

`env` sets literal variables every worker for this repo gets, on top of the worker's allowlist. `env_passthrough` names variables the server already has that the allowlist leaves out. Both are layered on the allowlist, which is not the server's whole environment: [Passed to workers](/reference/configuration/environment-variables#passed-to-workers) lists it, and [Added by the repo](/reference/configuration/environment-variables#added-by-the-repo) says what stays behind unless you name it here.

A sandboxed worker gets none of the allowlist, only what [Sandboxed workers](/reference/configuration/sandbox) lists, which covers the server's proxy, an extra CA, and each `env_passthrough` name, forwarded by name so its value never appears on the `docker` command line.

### `policy`

It takes any of:

- `allowed_tools`, `deny_tools`, `grants`, `sandbox`, `allowed_harnesses`, `escalation_harness`, `timeout_minutes` and `max_attempts`
- the four caps (`time_cap_minutes`, `total_time_cap_minutes`, `token_budget`, `budget_usd`), each the work item's own, within `maxima.work_item`

`deny_tools` is frozen into each work item when it is filed, and a later addition still applies to running items.

It binds every work item filed in this repo, whatever its chain. A value `policy.yaml` refuses makes [intake](/concepts/vocabulary#intake) refuse the item. See [Policy fields](/reference/configuration/policy#policy-fields).

### Repository steering

`steering: [name, ...]` names steering profiles in `library.yaml`, the same ones a task's `steering:` selects (see the [library reference](/reference/configuration/library-and-chains)). There is no other steering store.

- When a work item is filed, Kraft resolves each name to its steering profile's `instructions` and freezes the text into the item. Editing a steering profile reaches items filed afterwards and never one already filed.
- Every launch gets the repository's steering profiles first, then the task's, in the agent's system prompt under a `## Project standards` heading, 8 KB at most together.
- A name the library does not define is refused when the repository is saved (Settings › Repos) and when an item is filed. A library save that removes a steering profile a repository still names is refused too.
- You write steering profiles in Templates › Library, which edits `library.yaml`.

This is not a place for target-repo files. Kraft never copies `CLAUDE.md`, `AGENTS.md`, or anything else from inside the repo being worked on into what it tells an agent as a source of process context. The agent reads those files itself, the way it would for a person.

### Pull-request rules

_Added in next_

A repo can keep its own rules for driving a pull request in a skill: the checks to run before a change, how to regenerate generated files, what its reviews and CI expect. Claude Code reads the same file before it works a pull request in the cloud.

A task that declares the `pr_rules` input is told where that file is, by path, and to follow it. Kraft never copies the file into the prompt. It points at the first of these committed on the item's branch:

1. `.claude/skills/steward/SKILL.md`
2. `.agents/skills/steward/SKILL.md`
3. `.claude/skills/babysit/SKILL.md`
4. `.agents/skills/babysit/SKILL.md`

A file on disk that is not committed does not count, so a skill a setup step copies into the worktree is never taken for the repo's rules. With none of the four, the task is told nothing.

The shipped library declares `pr_rules` on the tasks that work a merge request: `repair_mr_feedback`, `triage_review_feedback`, `repair_mr_checks`, `repair_merge_queue_failure` and `describe_mr`. Where the rules say to push, merge or post to the pull request, the agent leaves that to Kraft.

### `sandbox`

`sandbox` is part of the repository policy layer. [Sandboxed workers](/reference/configuration/sandbox#the-sandbox-key) lists its keys, which go with `kind: docker` and which with `kind: kit` and which with `kind: os` ([The OS sandbox](/reference/configuration/sandbox/os-sandbox)), and says why no chain, node or task can change it. A sandbox needs a `network:` policy or `unrestricted_network: true`: a launch into one with neither is refused. See [Network policy](/reference/configuration/sandbox/network-policy#without-a-network-policy).

### `worktrees`
_Added in 2.1_

`worktrees: false` makes the repo's items run [in place](/guides/run/run-items-in-place): in the folder `path` names, on their own branches, one at a time, with no `setup_command` and no `local_files`. `kraft repo connect --in-place` writes it.

- An item may say otherwise when it is filed (`kraft item create --in-place` or `--worktree`), and its choice is frozen then. A later change here moves no item already filed.
- An entry with `worktrees: false` and a `sandbox` does not load: in-place items cannot be sandboxed. The message names both settings.
- A workspace item never runs in place: one filed on a `worktrees: false` root is refused until you file it with `--worktree`.

### Automated review

`automated_review` names one reviewer, or a list of them, each named one of two ways. They are the reviewers a chain's `mr.automated_review` task waits for.

- `bot: <login>` settles when that forge login has finished with the merge request's current head. How Kraft tells, and what it reads, depends on the chain: see [Waiting for several reviewers](#waiting-for-several-reviewers).
- `check: <name>` settles when that check run or commit status on the head completes. Success is clean. Failure is actionable, with its output as the finding.

Unset here and everywhere else, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for you rather than spending a repair.

Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet.

### Waiting for several reviewers
_Added in 2.4_

Reviewers can be named in three places. The nearest wins, and its list is the whole list:

1. The item's own `reviewers` override on the review node: `kraft item set-node-override --node N --reviewer bot:LOGIN`.
2. This repo's `automated_review`.
3. The review task's `reviewers` in the chain or library.

`[]` counts as naming reviewers, meaning none: `automated_review: []` turns the review off even when the chain's task lists bots. A reviewer with both `bot` and `check`, or neither, is refused when the file loads.

What the wait does with them depends on the chain.

- **The collect path.** When a later task in the same node declares the `bot_feedback` input, as the shipped triage step does, the wait holds until every reviewer has finished with the latest push, then hands what they said to that task. The wait never fails on feedback; the triage agent decides what needs a change.
- **Nothing reads the feedback.** When the node has no such task, as in a chain from before the triage step, or the item covers several repos, a bot is still waited for until it has finished with the push, as on the collect path. On a multi-repo item, each repo's bot is waited for against that repo's own push. Kraft then judges what was said itself:
  - Each unresolved thread, a newest review that requests changes (when the bot left no thread), and a check that finished without passing fail the wait and go to the fix loop. Anything else is clean.
  - On a multi-repo item, a member repo's findings give its paths from the item's own checkout, such as `repos/pkg/calc.py:3`, so the fix loop finds the file.
  - Kraft cannot tell what a bot's summary says, so a summary alone reads as clean, and the node's log names it as not read. `kraft admin doctor`'s `triage_review_feedback` row shows the edit that adds the triage step.
  - A check still unfinished at the timeout stops the item for you, as it would alone.
- **The direct path.** Reviewers that are only checks, when nothing collects them, are each read once per look. Any actionable feedback fails the wait at once, with every finding read in that check, and goes to the fix loop. A timeout stops the item for you.

A bot is finished with a push when it has been active since Kraft pushed that head and then quiet for the grace period, `wait.grace_period` on the task (60 seconds by default). Its activity is a new or edited comment, a review, an inline comment, a thread it resolved, or, on GitLab, an approval it gave or withdrew. Threads already open on the first check set a baseline and are not activity. A check is finished when it completes.

On the collect path, and for a bot that nothing reads, the timeout settles the wait: a bot that never posted had nothing to say, and the item goes on with what was collected. The timeout is recorded in the wait's events as `timed_out`.

Forge clocks are compared with Kraft's push time as they are, with no allowance for skew. A bot that posts a placeholder and edits it long after the grace period is read early; a longer `grace_period` on that chain is the fix.

### `ci_checks`

The two waits are a chain's `mr.ci` task before the merge and its `mr.post_merge_ci` task after it. Each records `ci_not_configured` when it passes at once because `ci_checks` is `false`. It does not touch `mr.automated_review` or `mr.external_approval`.

With `ci_checks` left on, `mr.post_merge_ci` still passes on a merge commit that has no CI run at all five minutes after its wait started, and records `post_merge_ci_absent`. That is a branch whose CI does not run on a push, such as a workflow on `pull_request` only, merged into a branch other than the default. A commit with any run, even a pending one, is waited for as before.

On GitHub, where Kraft reads only Actions runs, a commit whose CI reports from another app as a check run or commit status does not pass this way: the wait stays pending.

## In this section

- [How connect proposes commands](/reference/configuration/repos/connect-proposals): how `kraft repo connect` proposes setup and test commands, and what to do when it finds none.
- [detectors.yaml](/reference/configuration/repos/detectors): `detectors.yaml`, where you teach connect your own conventions, and the toolchains Kraft ships.
- [Workspaces](/reference/configuration/repos/workspaces): a root repository with other repositories mounted as submodules.
