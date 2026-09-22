# Configuration reference

Everything here lives under `$KRAFT_HOME/templates/` (default `~/.kraft/templates/`),
seeded from the packaged defaults on first run and never overwritten after — an
upgrade cannot clobber an edited policy. The Settings screens in the UI edit
these same files; hand-editing is equally supported (`kraft admin doctor`
reports anything that doesn't parse, and `kraft admin reload` picks up an
on-disk edit without a restart).

Every file in `templates/` is validated when it is loaded, against a model that
describes both its shape and its references — a chain component may only
`extends` a library component of its own kind, and a `reject_to` may only name a
node before the gate declaring it. A file that does not load is reported with
the offending key named. `kraft admin templates lint` checks the whole library
and every chain at once, and writes nothing.

`registry.yaml`, hook names and `gate_after` do not exist in Template Schema V1.
A home still holding them is refused until `kraft admin update` replaces it —
see [CLI → Service and admin](cli.md#service-and-admin).

## `library.yaml` — reusable components

Four sections, each a map of name to definition, which any chain takes with
`extends: <name>`:

| Section | Holds |
|---|---|
| `tasks` | Reusable tasks of any kind — `agent`, `subprocess`, `builtin`, `forge`. |
| `steps` | Reusable ordered groups of tasks. |
| `nodes` | Reusable nodes, with their own steps, `on_failure`, `fix_loop` and `escalation`. |
| `steering` | Named guidance an agent task selects with `steering: [name]`: `{instructions: "..."}`. |

```yaml
tasks:
  implementer:
    kind: agent
    harness: codex_default
    prompt: Implement the approved plan.
  code_review:
    kind: agent
    harness: claude_review
    prompt: Review this work item's change for defects its passing tests do not catch.
    skill: kraft:code-review
    inputs: [review_package]
```

An `agent` task's keys:

| Key | Means |
|---|---|
| `id` | Its local identifier (`[a-z][a-z0-9_-]*`); a library entry is named by its key instead. |
| `kind` | `agent`. |
| `harness` | The [harness profile](harnesses.md) it runs on — an id from `harnesses.yaml`. A disabled or missing profile stops the task for a human; nothing substitutes another. |
| `prompt` | What the task is asked to do. Kraft's own output contract is given before the skill and steering. |
| `skill` | One skill the agent is launched with, by name (`kraft:code-review`, or a plugin's `plugin:skill`). A skill that cannot be loaded stops the task for a human. |
| `steering` | Names from the library's `steering` section. |
| `produces` | The document kind it writes (`spec`, `plan`, `work_brief`, `review_brief`) — what a gate's `artifact` decides and an attachment covers. |
| `model` / `effort` | This task's runtime options, checked against what the profile's provider accepts. |
| `inputs` | What Kraft hands the task: `review_package` (the change under review), `carried_findings` (its previous round's findings) and `previous_review` (its previous session's result). |
| `scope` | `each_repository` fans the task out once per selected repository of a workspace item; the default runs once. |
| `on_failure` | A recovery pass for this task alone. |
| `policy` | This task's own policy layer. |
| `skippable` | `false` to refuse an operator's skip. |

A `subprocess` task has a `command`; a `builtin` task a `ref`
(`kraft.verify_changed_test_scopes`, which runs the repo's own test scopes); a
`forge` task a `target` (`mr.open_draft`, `mr.ci`, `mr.automated_review`,
`mr.mark_ready`, `mr.external_approval`, `mr.merge`, `mr.post_merge_ci`, …) and,
for a wait, `wait: {timeout: 90m, polling: {initial_interval: 30s,
max_interval: 5m}}`.

## `chains/*.yaml` — chain templates

One selectable chain per file, named by its `id` (or the file name). See
[Concepts](concepts.md#chain) for the node schema, with the shipped `default`
and `quick-task` chains as worked examples. `kraft admin templates show ID
--resolved` prints one with its library components expanded, and Settings →
Chains edits the file itself.

## `policy.yaml` — caps, budget, archiving

```yaml
loops: {}
default:             { attempts: 3, wall_clock_s: 3600 }

max_concurrent: 3
auto_escalate_stuck: true
auto_escalate_stuck_cap: 3
auto_escalate_delay_s: 0
auto_review_attempts: 1
forge_cli_timeout_s: 120

findings:
  loop_severities: [critical, important]

budget:
  work_item_usd: 10     # per work item, across every loop in its chain
  daily_usd: 50         # every work item on this instance, since local midnight

rate_limit_retries: 5

archive:
  after_days: 30

triggers:
  - cron: "0 9 * * 1,2,3,4,5"
    repo: /path/to/repo
    chain: default
    title: "Nightly dependency check"

# Template Schema V1: operational defaults and administrator maxima.
defaults:
  timeout_minutes: 60
  max_attempts: 3
  allowed_harnesses: [codex_default, claude_review]
maxima:
  timeout_minutes: 180
  token_budget: 2000000
  allowed_harnesses: [codex_default, claude_review]
```

The example sets no `maxima.allowed_tools`, on purpose: a safety field set
only in `maxima` binds every task, and codex and gemini cannot enforce a tool
list, so they refuse to launch under one — every `codex_default` task on the
shipped chain would stop.

| Key | Means |
|---|---|
| `loops.<name>` | `attempts` and `wall_clock_s` ceiling for one named loop. `default` covers anything not named explicitly, which today is every fix loop: a chain node's fix loop is keyed by the node's own canonical path (`verification.fix_loop`), not by a flat name, and a `max_attempts` on the loop itself wins over this key's `attempts`. A key naming no live loop is **silently unused** — `loops.get(key, default)` neither errors nor warns — so the shipped file names none. An external wait (a pipeline, an automated review, an approval, a merge landing) is not a loop: its timeout and polling are its task's own `wait:`, bounded by `maxima.wait_timeout_minutes`. |
| `max_concurrent` | How many work items may be `active` at once, across every repo, however they were started (`resume`, `retry`, or auto-intake). Moved here from `intake.yaml` — that file's copy is now a legacy fallback `load_policy` reads only when this key is absent. |
| `auto_escalate_stuck` | Whether a *stuck* stop auto-dispatches an escalation turn on a node that declares no `escalation` of its own. Stuck means a task that failed after recovery, an exhausted fix loop, a stall, or the fix-loop judge's `stop_needs_human`. A config error, a budget or rate limit, an infra stop, a reviewer error, a wait timeout or a question goes straight to a human, as do a pending gate and a spent reject loop. Independent of a node's own `auto_escalate` (gate review) — different mechanism, different trigger. Defaults on. |
| `auto_escalate_stuck_cap` | Attempts one `needs_human` run may be auto-escalated by `auto_escalate_stuck` before leaving it for a human — the stuck-escalation equivalent of a fix loop's `attempts`. |
| `auto_escalate_delay_s` | Seconds to wait after the triggering event before `auto_escalate` or `auto_escalate_stuck` fires, so a human already about to look isn't preempted by the agent. `0` (the default) fires immediately. |
| `auto_review_attempts` | How many automated-review attempts one pending gate may spend before it is left to a human. An attempt is counted whether the reviewer returned a verdict or was refused before it launched, so every non-terminating outcome suppresses the next poll. `1` (the default) is the one-attempt-per-gate behaviour this replaced a hardcoded boolean with. |
| `forge_cli_timeout_s` | Seconds one forge CLI call (`gh`, `glab`, `git`) may run before it is killed and reported as a forge error. Bounds a single call, not a wait (that is the task's own `wait:`). Default `120`. Read at startup. |
| `findings.loop_severities` | Which review-finding severities burn a fix cycle. Anything below that bar is recorded and shown at the human-review gate instead of silently discarded. |
| `budget.work_item_usd` / `budget.daily_usd` | Spend caps in dollars, both off by default (`null`). A cap refuses to *start* the next agent task — it cannot interrupt one already running, since cost is only known when a session exits, so overshoot is bounded by one task's cost. |
| `rate_limit_retries` | How many times Kraft auto-relaunches a work item after a rejected API rate limit before stopping for a human. Counts attempts, not wall-clock time — a rate-limit wait can run for hours. |
| `archive.after_days` | Completed/abandoned items older than this auto-archive. The board's Done group header states this number — keep them in sync if you change it. Defaults to `30` in the shipped template, but disables auto-archiving entirely (`None`/absent) if you remove the key rather than edit it. |
| `triggers` | Optional list of cron-fired chain starts. See [Inbound triggers](triggers.md). |
| `defaults` | Template Schema V1's inheritable operational starting points — `timeout_minutes`, `max_attempts`, `allowed_harnesses`. No safety meaning of their own: a repository, work item, chain or execution node may move any of them in either direction, bounded only by `maxima`. All optional; unset means unbounded. |
| `maxima` | The administrator ceiling on policy overrides — `timeout_minutes`, `max_attempts`, `allowed_harnesses`, `token_budget`, `allowed_tools` — and `wait_timeout_minutes`, the longest any external wait's `wait: timeout` may be (a longer one is refused when the item is filed; a wait with no timeout of its own gets 90 minutes or this, whichever is shorter). A safety field listed only here (`token_budget`, `allowed_tools`) starts *at* its maximum and can only ever be narrowed by an override. An unset maximum is no bound at all, which is what a fresh install ships with. A `defaults` entry past a `maxima` ceiling is refused when the file is read. |

**How V1 policy resolves and what it does.** A work item's policy is frozen
when it is filed, layered broadest first: `defaults`/`maxima` here, the
repository's `policy:` in `repos.yaml`, then the chain's, each node's, each
step's and each task's own `policy:`. A layer that relaxes what it inherits is
refused at intake, naming the scope. A
recovery plan inherits the task, step or node that declares it; a fix loop,
its judge, a node's escalation task and its conflict handler inherit their
node; a gate's `auto_review` inherits its gate. At runtime:

| Field | Rule down the layers | Enforced where |
|---|---|---|
| `allowed_tools` | only narrows | The permission gate answers a worker's ask from it, and it is passed as `--allowedTools`. Unset (no layer sets it) allows every tool; `[]` allows none. A harness with no tool-list capability (codex, gemini) refuses to launch under one rather than run unrestricted. |
| `deny_tools` | only accumulates | Denied by the permission gate and passed as `--disallowed-tools`, on top of `allowed_tools`. |
| `sandbox` | set once, never changed or removed | Wraps the task's process (agent, subprocess, builtin) in `docker run`. |
| `token_budget` | only narrows | Before each agent launch, gate reviewers included: once the work item's sessions have spent this many tokens (input plus output) the next agent task is refused and the item stops for a human. Like `budget`, it cannot interrupt a running agent. |
| `allowed_harnesses` | within `maxima` | An agent task selecting another profile is refused at intake, and again at launch. |
| `max_attempts`, `timeout_minutes` | within `maxima`; execution node or broader only | Bound the node's fix loop (attempts, wall clock). The loop's own `max_attempts` and an operator's per-item node override win over them; they win over `loops:`/`default:`. A step, task or gate refuses them. |

## `harnesses.yaml` — harness profiles

A harness *profile* is a configured instance of an agent-runtime provider: which
executable to run, and what runtime options to start from. It is never provider
command syntax or result parsing — the provider package
(`src/kraft/harnesses/<provider>.yaml`) declares the capability surface, and a
profile selects only from it. A `defaults` key the provider does not declare, or
a value it does not accept, is refused when the file is read.

Every agent task's `harness:` names a profile here, never a provider directly,
and the file is read again at each agent launch, so an edit reaches the next
one without a restart. A task whose profile is missing or disabled, or whose
`harnesses.yaml` cannot be read, stops for a human with the reason in its
session log; Kraft never falls back to another profile or to a provider of the
same name.

```yaml
harnesses:
  codex_default:
    provider: codex
    enabled: true
    executable: codex
    defaults:
      effort: medium

  claude_review:
    provider: claude
    enabled: true
    executable: claude
    defaults:
      model: sonnet
```

| Key | Means |
|---|---|
| `<profile id>` | The name a V1 task's `harness:` selects. Lowercase, digits, `_` and `-`. |
| `provider` | The harness this profile configures. Must be an installed harness id (`claude`, `codex`, `gemini`) — the provider id *is* the harness id. |
| `enabled` | `false` takes the profile out of service. A task selecting a disabled profile stops for a human; Kraft never substitutes another. Defaults `true`. |
| `executable` | The command to launch, when it differs from the provider's own default. It replaces the executable only: the provider's own subcommand (`codex exec`) is kept after it. |
| `defaults` | Runtime options every task using this profile starts from: `model`, `effort` and `permission_mode`. Checked against what the provider declares it accepts. They are the lowest rung: a work item's own override, the task's own field and the repo's `default_model` all win over them. Any other key stops the task for a human rather than being ignored. |

## `repos.yaml` — connected repos

```yaml
repos:
  - path: /home/you/code/my-service
    name: my-service
    default_chain_template: default
    forge: github
    test_command: null
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
| `path` | *(required)* | Absolute path to the repo. The only field with no default. |
| `name` | — | Display name; set at connect time, not otherwise validated. |
| `id` | — | The repository id a workspace names this entry by (`[a-z][a-z0-9_-]*`, unique). Only a workspace's root and members need one; connecting a repo with submodules writes it for them. |
| `managed` | `true` | Keeps a human-connected repo out of Settings' "Detected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly. |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `fake` is **dev-only**: an in-process forge that opens nothing, which `just dev`'s seeded repo uses. `null` at load time — `kraft repo connect` is what actually resolves it, from the repo's remote. |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. Renamed from the legacy `gitlab_project` key, which a hand-edited file may still carry — read transparently, never rewritten out from under you. |
| `models` | `{}` | The model an agent task runs with on this repo, per harness profile id (`claude_review: opus`): above the profile's own `defaults:`, below a task's `model:` and the work item's override. Keyed by profile because one model name means nothing to another provider. Replaces the retired `default_model`, which a loaded file drops with a warning. |
| `test_command` | `null` | The command CI actually runs for this repo — what the changed-test-scope verification runs, as one scope over every path. A repo with neither this nor `test_scopes` stops that verification for a human rather than inventing a command. |
| `areas` | `{}` | Path-scoped contexts inside this repo, keyed by id: `{paths: [...], setup: "...", verification: {test_scopes: [...]}}`. An area's test scopes join the repo's and are selected by changed paths the same way; its `setup` runs once before the first of its scopes runs. Areas are never forge targets. |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. Not synthesized from `test_command` — the two stay independently editable. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. `""` means "deliberately nothing"; an absent value stops the repo's next work item rather than guessing. |
| `env` | `{}` | Literal environment variables every worker for this repo gets, layered onto the worker baseline allowlist. |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the baseline allowlist doesn't cover. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. |
| `deny_tools` | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer (below): frozen into each work item when it is filed, and a later addition still applies to running items. |
| `steering` | `[]` | Steering docs (from `templates/steering/`) attached to every agent task on this repo, beside the task's own library steering. |
| `sandbox` | `null` | `{kind: docker, image: ...}` — run this repo's task processes in that container. Part of the repository policy layer: once set, no chain, node or task can turn it off, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both. |
| `policy` | `null` | The repository policy layer: any of `allowed_tools`, `deny_tools`, `sandbox`, `token_budget`, `allowed_harnesses`, `timeout_minutes`, `max_attempts`, applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. It binds every work item filed in this repo, whatever its chain; a value `policy.yaml` refuses makes intake refuse the item. See `policy.yaml`'s table above. |
| `automated_review` | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for, named exactly one way. `bot: <login>` settles when that forge login has reviewed the merge request's current head: on GitHub, changes requested or any inline comment is actionable (one finding per comment) and anything else is clean; on GitLab, the bot's unresolved discussions are actionable and its approval is clean. `check: <name>` settles when that check run or commit status on the head completes: success is clean, failure is actionable with its output as the finding. Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair. A dismissed GitHub review doesn't count. Kraft reads only the first page of 100 of each list it asks for: the pull request's reviews, a review's comments, and a GitLab merge request's discussions and commit statuses. A bot with no match on that first page reads as not having reviewed yet. On GitLab an approval isn't tied to a commit, so a bot's approval of an earlier head still reads as clean, unless the project resets approvals on push. |

No key on an entry passes silently. A key within two edits of a field above (`automated_reviews:`) is refused when the file loads, naming the field it meant. Any other key the table doesn't list is kept, logged as a warning, and fails `kraft admin doctor` until it is removed. The exceptions are `name`, `enabled` and `default_chain_template`, which Kraft writes itself.

`kraft repo connect` probes a `setup_command` from the repo's markers; check it
before trusting it, and `kraft admin doctor` reports any connected repo still
missing one.

### Workspaces

A workspace is a root repository with other repositories mounted in it as
submodules. It is declared beside the `repos:` list, naming entries by `id`:

```yaml
workspaces:
  product:
    root: product
    root_pointer_default: ignore     # or bump
    members:
      api: { repository: api, path: services/api }
```

Connecting a repo that has submodules declares its workspace for you, each
submodule a member. When you file a work item against the root, you choose which
members it changes and its root-pointer policy (the workspace's default unless
you pick one); both are frozen into the item. The item's worktree holds exactly
those members, each on the item's branch, and a task with `scope:
each_repository` runs once per selected repository.

Each selected repository binds the tasks that run in it with its own policy
layer. A task in the assembled worktree, which holds all of them at once, runs
under the tightest of their layers: allowlists intersect, deny lists add up, and
numbers take their minimum.

A workspace with members can't be sandboxed at all. A sandboxed worker can write
each submodule's git directory, so it could plant a hook or `core.sshCommand`
there that host git would run as you when Kraft pushes the submodule. If the
root or any member sets `sandbox` (or `policy.sandbox`), `repos.yaml` fails to
load, connecting the root is refused, filing an item is refused, and `kraft
admin doctor` fails that repository's `sandbox` row. A sandbox that comes from a
chain, node or task is refused the same way when the item is filed or retried.
An item already in flight when a sandbox appears stops for a person before
Kraft runs any git in its worktree.

Publication goes members first. A member's merge request merges before the root
moves. A root with source changes of its own gets its own merge request, and it
stays a draft until every member has merged and the root names their merged
revisions. A root whose only change is the members' pointers follows the item's
root-pointer policy: `ignore` leaves it alone, and `bump` pushes the new pointers
to its default branch. If that push is refused, it opens a merge request for them
instead. A member that fails to merge stops publication and leaves the root
untouched.

Upgrading: a `repos.yaml` from before workspaces still loads. `default_model`
and `default_root_merge_policy` are dropped with a warning. Items with submodules
now need a declared workspace: add the `workspaces:` block and the `id`s by
hand, or disconnect the root and connect it again (which loses its settings).

## `access.yaml` — bind, password, remote access

```yaml
bind: 127.0.0.1
port: 8765
password_hash: null
session_expiry_days: 7
allowed_hosts: []
```

| Field | Default | Means |
|---|---|---|
| `bind` | `127.0.0.1` | The address `kraft admin start` binds. A non-loopback value is refused unless `password_hash` is set. |
| `port` | `8765` | Flag (`--port`) overrides this, which overrides the env var, which overrides this file. |
| `password_hash` | `null` | Set from Settings → Access while still on `127.0.0.1`. Required for any non-loopback bind. |
| `session_expiry_days` | `7` | How long a browser session cookie stays valid after logging in. |
| `allowed_hosts` | `[]` | `Host` header allowlist checked on a non-loopback bind — a password alone does not authorize an arbitrary hostname. See [Remote access](remote-access.md). |

## `intake.yaml` — autonomous pickup

Off by default. When enabled, Kraft polls [`bd ready`](https://github.com/gastownhall/beads)
and files matching beads as paused work items without anyone typing
`kraft item create` — same "lands paused, no gate skipped" guarantee as every
other intake path.

```yaml
enabled: false
interval_s: 300
repos: []              # empty means every enabled repo in repos.yaml
priority_ceiling: 2    # only P2 and below start unattended
```

`max_concurrent` used to live here; it's read from
[`policy.yaml`](#policyyaml-caps-budget-archiving) now, since auto-intake was
never the only door onto a running item. A value still set here is a legacy
fallback, used only when `policy.yaml` doesn't have the key.

An auto-started work item passes no gate automatically — it runs to its first
gate and stops for a human exactly as a typed-in one does. Auto-intake removes
the typing, not the judgement. Editable here or from Settings → Auto-intake,
which applies a change without a restart.
