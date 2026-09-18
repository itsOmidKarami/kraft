# Configuration reference

Everything here lives under `$KRAFT_HOME/templates/` (default `~/.kraft/templates/`),
seeded from the packaged defaults on first run and never overwritten after — an
upgrade cannot clobber an edited policy. The Settings screens in the UI edit
these same files; hand-editing is equally supported (`kraft admin doctor`
reports anything that doesn't parse, and `kraft admin reload` picks up an
on-disk edit without a restart).

## Chain templates (`*.yaml`)

Any YAML file in `templates/` whose top level is `id:` + `nodes:` is a chain
template, selectable by that `id` when creating a work item. See
[Concepts](concepts.md#chain) for the node schema (`gate_after`, `fix_loop`,
`on_failure`, `rebase_bounce_to`, `reject_to`, `auto_escalate`) with the
shipped `default` and `quick-task` templates as worked examples.

## `registry.yaml` — hook point bindings

Maps every hook point a chain can name to the adapter that runs it:

```yaml
defaults:
  agent: { steering: [never-signal-processes-you-didnt-start] }
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.spec.requested:       { kind: agent,      command: claude, skill: spec, artifact: spec }
  on.test.run:             { kind: subprocess, command: [uv, run, pytest, -q] }
  on.mr.open:              { kind: forge,      handler: open_mr, backend: auto }
```

| Key | Applies to | Means |
|---|---|---|
| `kind` | every entry | `agent`, `subprocess`, `builtin`, or `forge` — see [Concepts](concepts.md#hook-point-and-adapter). |
| `command` | `agent`, `subprocess` | The binary to run — `claude` for an agent hook, an argv list for a subprocess hook. |
| `skill` | `agent` | A named skill the agent is launched with (`spec`, `plan`, `code-review`, `mr-metadata`, ...). |
| `artifact` | `agent` | What the hook is expected to produce — a spec, a plan, a review brief — surfaced on the gate that follows it. |
| `model` / `effort` | `agent` | Per-hook overrides of the agent's model and effort, where the default isn't right for that step. |
| `handler` | `builtin`, `forge` | Which Python function or forge operation runs. |
| `backend` | `forge` | `auto` resolves per repo from the `forge` field on that repo's `repos.yaml` entry — never pin a forge here, or every repo on the install is forced onto one. |
| `defaults.agent.steering` | top level | Steering docs (from `templates/steering/`) attached to every agent hook by default, layered under any a repo or work item adds. |

Rebinding a hook — say, pointing `on.test.run` at a different command, or
`on.review.local.run` at a different skill — is an edit here, not a chain
template change. `on.review.security.run` ships registered but in no default
chain; a plan touching auth/sessions/tokens/secrets adds it to `verify`
dynamically.

## `policy.yaml` — caps, budget, archiving

```yaml
loops:
  verify_fix_loop:   { attempts: 3, wall_clock_s: 3600 }
  ci_fix_loop:       { attempts: 3, wall_clock_s: 3600 }
  ci_wait:           { attempts: 60, wall_clock_s: 1800 }
  rebase_bounce:     { attempts: 2, wall_clock_s: 3600 }
  rebase_conflict:   { attempts: 3, wall_clock_s: 3600 }
default:             { attempts: 3, wall_clock_s: 3600 }

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
```

| Key | Means |
|---|---|
| `loops.<name>` | `attempts` and `wall_clock_s` ceiling for a named fix loop, referenced by a node's `fix_loop`. `default` covers anything not named explicitly. `rebase_conflict` bounds the conflict-resolving agent `/resume`, `/retry`, or `pre_mr_rebase` can dispatch. |
| `findings.loop_severities` | Which review-finding severities burn a fix cycle. Anything below that bar is recorded and shown at the human-review gate instead of silently discarded. |
| `budget.work_item_usd` / `budget.daily_usd` | Spend caps in dollars, both off by default (`null`). A cap refuses to *start* the next agent task — it cannot interrupt one already running, since cost is only known when a session exits, so overshoot is bounded by one task's cost. |
| `rate_limit_retries` | How many times Kraft auto-relaunches a work item after a rejected API rate limit before stopping for a human. Counts attempts, not wall-clock time — a rate-limit wait can run for hours. |
| `archive.after_days` | Completed/abandoned items older than this auto-archive. The board's Done group header states this number — keep them in sync if you change it. |
| `triggers` | Optional list of cron-fired chain starts. See [Inbound triggers](triggers.md). |

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
```

| Field | Default | Means |
|---|---|---|
| `path` | *(required)* | Absolute path to the repo. The only field with no default. |
| `managed` | `true` | Keeps a human-connected repo out of Settings' "Detected" section; auto-connected submodules are written with `managed: false`. |
| `default_chain_template` | — | Which chain template a work item on this repo uses when none is named explicitly. |
| `forge` | resolved from remote | `github` or `gitlab` — which forge adapter `backend: auto` resolves to for this repo. |
| `default_model` | `null` | Overrides the agent model for every hook on this repo, where set. |
| `test_command` | `null` (falls back to the registry's `on.test.run`) | The command CI actually runs for this repo — lets `verify`'s local test run and CI's differ deliberately, rather than drift apart by accident. |
| `setup_command` | *(required — no fallback)* | Run in every new worktree before any node starts. `""` means "deliberately nothing"; an absent value stops the repo's next work item rather than guessing. |
| `env` | `{}` | Literal environment variables every worker for this repo gets, layered onto the worker baseline allowlist. |
| `env_passthrough` | `[]` | Names of variables to carry over from the daemon's own environment, for what the baseline allowlist doesn't cover. |
| `local_files` | `[]` | Relative paths (no globs, no directories) to copy into every new worktree — for files `git worktree add` can't carry, like an untracked `.python-version`. |
| `default_root_merge_policy` | `bump` | How a submodule bump at this repo's root is handled by default. |
| `deny_tools` | `[]` | Tool names withheld from every agent hook on this repo. |
| `steering` | `[]` | Steering docs (from `templates/steering/`) attached to every agent hook on this repo, layered under the registry's own defaults. |
| `sandbox` | `null` | Sandbox policy for this repo's worker processes, if set. |

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
max_concurrent: 3
repos: []              # empty means every enabled repo in repos.yaml
priority_ceiling: 2    # only P2 and below start unattended
```

An auto-started work item passes no gate automatically — it runs to its first
gate and stops for a human exactly as a typed-in one does. Auto-intake removes
the typing, not the judgement. Editable here or from Settings → Auto-intake,
which applies a change without a restart.
