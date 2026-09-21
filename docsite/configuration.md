# Configuration reference

Everything here lives under `$KRAFT_HOME/templates/` (default `~/.kraft/templates/`),
seeded from the packaged defaults on first run and never overwritten after — an
upgrade cannot clobber an edited policy. The Settings screens in the UI edit
these same files; hand-editing is equally supported (`kraft admin doctor`
reports anything that doesn't parse, and `kraft admin reload` picks up an
on-disk edit without a restart).

Every file in `templates/` is validated when it is loaded, against a model that
describes both its shape and its references — a chain node may only name a hook
the registry defines, and a `reject_to` may only name a node at or before the
one declaring it. A file that does not load is reported with the offending key
named, and the rest of the configuration keeps working: one broken chain
template does not stop the server.

## Chain templates (`*.yaml`)

Any YAML file in `templates/` whose top level is `id:` + `nodes:` is a chain
template, selectable by that `id` when creating a work item. See
[Concepts](concepts.md#chain) for the node schema (`tasks`/`steps`,
`gate_after`, `fix_loop`, `on_failure`, `rebase_bounce_to`, `reject_to`,
`auto_escalate`, `auto_escalate_stuck`, `auto_escalate_delay_s`) with the
shipped `default` and `quick-task` templates as worked examples, and
[Concepts → Composing a template](concepts.md#composing-a-template) for
building a custom one with `extends`/`remove`/`insert_before`/`insert_after`
instead of restating a whole node list.

## `registry.yaml` — hook point bindings

Maps every hook point a chain can name to the adapter that runs it:

```yaml
defaults:
  agent: { steering: [never-signal-processes-you-didnt-start] }
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.spec.requested:       { kind: agent,      harness: claude, skill: spec, artifact: spec }
  on.test.run:             { kind: subprocess, command: [uv, run, pytest, -q] }
  on.mr.open:              { kind: forge,      handler: open_mr, backend: auto }
  on.ci.poll:              { kind: forge,      handler: ci_poll, backend: auto, on_failure: [on.ci.repair] }
```

| Key | Applies to | Means |
|---|---|---|
| `kind` | every entry | `agent`, `subprocess`, `builtin`, or `forge` — see [Concepts](concepts.md#hook-point-and-adapter). |
| `harness` | `agent` | Which agent runtime to launch — `claude` (the default), `codex`, `gemini`, or an operator-added one. See [Agent harnesses](harnesses.md). |
| `command` | `subprocess` | The argv to run, e.g. `on.test.run`'s `[uv, run, pytest, -q]`. |
| `skill` | `agent` | A named skill the agent is launched with (`spec`, `plan`, `code-review`, `mr-metadata`, ...). |
| `artifact` | `agent` | What the hook is expected to produce — a spec, a plan, a review brief — surfaced on the gate that follows it. |
| `model` / `effort` | `agent` | Per-hook overrides of the agent's model and effort, where the default isn't right for that step. |
| `handler` | `builtin`, `forge` | Which Python function or forge operation runs. |
| `backend` | `forge` | `auto` resolves per repo from the `forge` field on that repo's `repos.yaml` entry — never pin a forge here, or every repo on the install is forced onto one. |
| `on_failure` | every entry | Hook points to run when *this task* fails, before the task is re-dispatched on its own. The repair travels with the binding, so every chain that runs the task gets it — `on.ci.poll` ships with `on_failure: [on.ci.repair]`. A repair is believed only when the task passes on the re-dispatch, never on the repair's own say-so. One layer deep: a repair hook's own `on_failure` is never dispatched, and a hook may not name itself. |
| `defaults.agent.*` | top level | Applied to every `kind: agent` hook that doesn't set its own value. Any of `harness`, `profile`, `model`, `escalate_model`, `effort`, `permission_mode`, `skill`, `artifact`, `command` (overrides the harness executable), `interactive`, and `timeout` (seconds one agent task may run before it is stopped) — plus three that merge as a list instead of binding-wins: `steering` (from `templates/steering/`), `deny_tools`, `allowed_tools` (default's items first, then the binding's own, deduped). Setting `artifact` or `skill` here applies it to *every* agent hook, which is rarely what you want — both are usually per-hook. |

Rebinding a hook — say, pointing `on.test.run` at a different command, or
`on.review.local.run` at a different skill — is an edit here, not a chain
template change. `on.review.security.run` ships registered but in no default
chain; a plan touching auth/sessions/tokens/secrets adds it to `verify`
dynamically.

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
  allowed_tools: [git, shell, editor]
  allowed_harnesses: [codex_default, claude_review]
```

| Key | Means |
|---|---|
| `loops.<name>` | `attempts` and `wall_clock_s` ceiling for one named loop. `default` covers anything not named explicitly, which today is every fix loop: a chain node's fix loop is keyed by the node's own canonical path (`verification.fix_loop`), not by a flat name, and a `max_attempts` on the loop itself wins over this key's `attempts`. A key naming no live loop is **silently unused** — `loops.get(key, default)` neither errors nor warns — so the shipped file names none. An external wait (a pipeline, an automated review, an approval, a merge landing) is not a loop: its timeout and polling are its task's own `wait:`, bounded by `maxima.wait_timeout_minutes`. |
| `max_concurrent` | How many work items may be `active` at once, across every repo, however they were started (`resume`, `retry`, or auto-intake). Moved here from `intake.yaml` — that file's copy is now a legacy fallback `load_policy` reads only when this key is absent. |
| `auto_escalate_stuck` | Whether a `needs_human` stop for a reason *other than* a pending gate (e.g. a stuck fix loop) auto-dispatches an escalation turn. Independent of a node's own `auto_escalate` (gate review) — different mechanism, different trigger. Defaults on. |
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
    default_root_merge_policy: bump
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
| `managed` | `true` | Keeps a human-connected repo out of Settings' "Detected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly. |
| `forge` | `null` | `github` or `gitlab`, which forge adapter `backend: auto` resolves to for this repo. `fake` is **dev-only**: an in-process forge that opens nothing, which `just dev`'s seeded repo uses. `null` at load time — `kraft repo connect` is what actually resolves it, from the repo's remote. |
| `project` | `null` | The GitLab project path, when `forge: gitlab`. Renamed from the legacy `gitlab_project` key, which a hand-edited file may still carry — read transparently, never rewritten out from under you. |
| `default_model` | `null` | Overrides the agent model for every hook on this repo, where set. |
| `test_command` | `null` (falls back to the registry's `on.test.run`) | The command CI actually runs for this repo — lets `verify`'s local test run and CI's differ deliberately, rather than drift apart by accident. |
| `test_scopes` | `null` | A monorepo's per-directory test commands: a list of `{paths: [...], command: "..."}` mappings, each `paths` non-empty and each `command` a non-empty string. Not synthesized from `test_command` — the two stay independently editable. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. `""` means "deliberately nothing"; an absent value stops the repo's next work item rather than guessing. |
| `env` | `{}` | Literal environment variables every worker for this repo gets, layered onto the worker baseline allowlist. |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the baseline allowlist doesn't cover. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. |
| `default_root_merge_policy` | `bump` | How a submodule bump at this repo's root is handled by default. |
| `deny_tools` | `[]` | Tool names withheld from every agent task on this repo. Part of the repository policy layer (below): frozen into each work item when it is filed, and a later addition still applies to running items. |
| `steering` | `[]` | Steering docs (from `templates/steering/`) attached to every agent hook on this repo, layered under the registry's own defaults. |
| `sandbox` | `null` | `{kind: docker, image: ...}` — run this repo's task processes in that container. Part of the repository policy layer: once set, no chain, node or task can turn it off, and `false` here cannot turn off one a layer set. Set it here or in `policy.sandbox`, not both. |
| `policy` | `null` | The repository policy layer: any of `allowed_tools`, `deny_tools`, `sandbox`, `token_budget`, `allowed_harnesses`, `timeout_minutes`, `max_attempts`, applied after `policy.yaml` and before the chain, and only ever tightening what `policy.yaml` allows. It binds every work item filed in this repo, whatever its chain; a value `policy.yaml` refuses makes intake refuse the item. See `policy.yaml`'s table above. |
| `automated_review` | `null` | The one automated reviewer a chain's `mr.automated_review` task waits for, named exactly one way. `bot: <login>` settles when that forge login has reviewed the merge request's current head: on GitHub, changes requested or any inline comment is actionable (one finding per comment) and anything else is clean; on GitLab, the bot's unresolved discussions are actionable and its approval is clean. `check: <name>` settles when that check run or commit status on the head completes: success is clean, failure is actionable with its output as the finding. Unset, the repository expects no automated review: the task settles clean at once and records `automated_review_not_configured`. A reviewer that errors stops the item for a person rather than spending a repair. |

`kraft repo connect` probes a `setup_command` from the repo's markers; check it
before trusting it, and `kraft admin doctor` reports any connected repo still
missing one.

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
