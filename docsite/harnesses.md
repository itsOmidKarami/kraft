# Agent harnesses

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent` task in [`library.yaml`](configuration.md#libraryyaml-reusable-components)
names a harness *profile* in its `harness:` field, and `harnesses.yaml` says
which harness (the profile's `provider`) that profile runs:

```yaml
spec_author: { kind: agent, harness: codex, prompt: "...", produces: spec }
```

Kraft ships three harnesses:

| id | Binary | Notable gaps |
|---|---|---|
| `claude` | `claude` | Full capability set. |
| `codex` | `codex exec` | No `deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `autocompact`, or `rate_limit_signal` — a profile or task asking for one of those is rejected at load. |
| `gemini` | `gemini` | No out-of-band context channel (context goes in-band via the prompt), no `effort`, no `resume` at all (Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability isn't declared). |

## Capabilities, not flags

A task's YAML never names a harness's actual CLI flags. It asks for a
**capability** — `prompt`, `context`, `model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `resume`,
`autocompact`, `structured_log`, `usage`, `rate_limit_signal` — and each harness's own YAML
(`src/kraft/harnesses/*.yaml` in the package) maps that capability onto
whatever its CLI actually calls it. `permission_mode` is `--permission-mode
acceptEdits|auto|...` for Claude, `-s read-only|workspace-write|...` for
Codex, `--approval-mode default|yolo|...` for Gemini — one Kraft-side name,
three different flags.

Three capabilities are required — `prompt`, `context`, `usage` — since no
agent dispatch can be built without them. Two are non-invocable —`usage`,
`rate_limit_signal` — they describe what Kraft reads back out of a session
(from its structured log or a result file), not an argv it constructs.

Some harnesses declare `values:` on a capability — a closed vocabulary the
CLI itself would reject (Codex's `effort` is `minimal, low, medium, high`,
Claude's is `low, medium, high, xhigh, max`) — checked at load time, and
`always:` — a value Kraft forces regardless of what a binding asks for
(Gemini's `permission_mode` is always `yolo`: the disposable worktree is the
real safety boundary, not the approval mode, and a headless worker has nobody
to answer an approval prompt anyway).

## Agent profiles

An **agent profile** is a named model tier (`deep`, `strong`, `fast`) that a
task selects with `profile:` instead of spelling out `model:` and `effort:`.
It lives in `harnesses.yaml` under `profiles:`, beside the harness profiles,
and it doesn't depend on the harness: `deep` is the same tier on Claude Code or
Codex, with the model id spelled per provider.

```yaml
# harnesses.yaml
profiles:
  deep:
    effort: high
    model: { claude: opus, codex: gpt-5.6-sol }
  strong:
    effort: high
    model: { claude: sonnet, codex: gpt-5.6-terra }
  fast:
    effort: low
    model: { claude: haiku }
```

```yaml
# library.yaml
tasks:
  implementer: { kind: agent, harness: claude, profile: strong, prompt: "..." }
```

- `model` is keyed by **provider** id, not harness id. Two harnesses on one
  provider (`claude-work`, `claude-personal`) share one spelling.
- A profile may leave a provider out. It then can't run on a harness of that
  provider. Only a task that actually pairs the two is refused, so a new
  provider never breaks an existing profile. Kraft never guesses a model for
  a provider the profile doesn't name.
- A task takes one route: `profile:`, or its own `model:`/`effort:`. It can't
  set both. Through `extends`, the nearer layer's route wins whole. A task that
  sets `profile:` drops the `model`/`effort` it inherits, and one that sets
  `model:` or `effort:` drops the inherited `profile`.
- The profile takes the rung the task's own `model`/`effort` would. The node
  override, the item override and escalation still beat it. It beats the
  repo's `models:` and the harness's `defaults:`.
- The task's profile **name** is frozen with the chain at intake. The profile's
  **body** is read from `harnesses.yaml` at every launch, so editing `deep`
  changes every later launch that selects it, including on items already
  running.

Kraft checks each pairing: the profile exists, names a model for the task's
harness's provider, and that provider accepts the model and the effort. A bad
pairing is reported, naming the chain, task, profile, provider and harness
(`chain 'default' task 'implementation.main.implement': profile 'fast' has no
model for provider 'codex' (harness 'codex')`), on Settings → Harnesses and in
`kraft admin harnesses`. It's also a failing `kraft admin doctor` check, and
Kraft refuses a harness save that would cause one. At launch, a task with a
bad pairing stops for a human with the same text. No other model is
substituted.

The shipped `harnesses.yaml` has the three tiers above. The shipped library
puts `implementer`, the three `repair_*` tasks and `strict_judge` on
`profile: strong`, which is the same `sonnet` at `high` effort they launched
with before. An install seeded earlier keeps its own files and runs unchanged.
To adopt profiles there, copy the `profiles:` section into your
`harnesses.yaml` and set `profile:` on a task in place of its
`model:`/`effort:`. `kraft admin doctor` lists this under what the install is
missing.

## Overriding or adding one

An operator drops a same-named YAML file into `$KRAFT_HOME/templates/harnesses/`
to override a shipped harness (say, `claude`'s `--model` allowlist) or add a
new one entirely. Not seeded by the usual `templates/` copy-on-first-run —
a seeded copy would freeze at whichever version was installed when Kraft
first ran, so this directory only exists once someone has deliberately put
something in it.

`kraft admin doctor` runs one PATH check per harness profile the live
library's chains actually select (not every declared one — an install whose
chains never select a `codex` profile isn't told to go install `codex`), plus a
failure row for any harness file that failed to load at all.

## Adding one

A new harness is a YAML file at `$KRAFT_HOME/templates/harnesses/<id>.yaml`
(the same override directory as above) — no Python change, no Kraft release.
Required top level:

```yaml
id: mytool          # must match the filename's stem
kind: cli            # the only kind implemented; a second kind gets its own adapter
command: [mytool]    # argv prefix
capabilities:
  prompt:   { cli: ["-p", "{value}"] }
  context:  { channel: prompt }   # or: { channel: system_prompt, cli: [...] }
  usage:    { source: result_file }   # or: { source: envelope, reader: <a Python parser's name> }
```

`prompt`, `context`, and `usage` are required — nothing can dispatch without
them. Every other capability (`model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `resume`,
`autocompact`, `structured_log`, `rate_limit_signal`) is optional: omit what the CLI can't
do, and a binding naming it is rejected at load, pointing at this file.

A capability needs a `cli:` argv fragment unless it's `usage`/
`rate_limit_signal` (read back out, not invoked) or `context` with
`channel: prompt` (folded into the prompt text itself, not a separate flag).
`{value}` and `{csv}` are the whole placeholder language — a comma-joined
list for `{csv}`, a single substituted string for `{value}`. Needing a third
kind of substitution is a sign the harness belongs in code, not YAML.

Two more fields keep a binding from doing something the CLI would reject:
`values:` (a list of `re.fullmatch` patterns — a value outside them fails at
load, not at launch) and `always:` (a value Kraft forces regardless of what a
binding asks for, checked against `values:` too, since a default the CLI
itself would reject is worse than no default). `resume` can bind `via:
command_resume` instead of `cli:`, when a resume needs its own command
prefix rather than a trailing flag (see `codex.yaml`'s `command_resume:
[codex, exec, resume, "{value}"]`).

A launch whose policy sets `allowed_tools` (an empty list included) must not
let a tool outside the list run, and pre-approving the listed ones is not
that. It needs two things from its harness: `restrict_tools`, the CLI's own
flag for which built-in tools exist (Claude's `--tools`, given the built-in
names from the list), and a `permission_mode` with an `under_allowlist:` mode
that asks the approval channel about everything else instead of approving it
(Claude's `manual`; its `auto` lets a classifier approve an unlisted tool
without asking). A harness missing either, `permission_mode` included,
refuses to launch under an allowlist, and so does a launch whose own
`permission_mode` differs from that mode.

Validate with `kraft admin doctor` — it loads every harness a live binding
names and reports a PATH check for each, plus the load error for any file
that failed outright.

## Fallback

A task that must not wait out a rate limit can name where its launch goes
next. A `fallback:` list is ordered. Each entry may name a `harness:` (a
harness profile id from `harnesses.yaml`) plus one route, the same either-or a
task obeys: an agent `profile:`, or a `model:` and/or `effort:`. It keeps
whatever it omits from the task's own launch.

The list lives on an agent profile, as the tier's default, and a task may set
its own, which replaces the profile's whole (`fallback: []` means none, even
under a profile that has one). The recommended start:

```yaml
# harnesses.yaml
profiles:
  deep:
    effort: high
    model: { claude: opus, codex: gpt-5.6-sol }
    fallback:
      - { harness: codex }            # same tier, other harness
      - { profile: strong }           # other tier, same harness
  strong:
    effort: high
    model: { claude: sonnet, codex: gpt-5.6-terra }
```

```yaml
# library.yaml
tasks:
  legacy_task:
    kind: agent
    harness: claude
    model: opus
    effort: high
    fallback:
      - { model: sonnet }                          # claude, sonnet, high
      - { harness: codex, model: gpt-5.6-terra }   # codex, gpt-5.6-terra, high
```

| entry | on `claude` + `profile: deep` | on `claude` + `model: opus`, `effort: high` |
|---|---|---|
| `{ harness: codex }` | codex + deep: `gpt-5.6-sol`, high | codex + `opus`: refused, codex takes no `opus` |
| `{ profile: strong }` | claude + strong: `sonnet`, high | claude + strong |
| `{ model: sonnet }` | claude + `sonnet`, no effort | claude + `sonnet`, high |

An entry's own profile's list is not followed: lists do not chain. Like the
profile body, a profile's list is read live at each launch. Through `extends`,
a child task's `fallback:` replaces its parent's.

Every entry of every list a chain's task uses must pair with its harness, the
same check a task's own route gets: in Settings → Harnesses (which shows each
profile's list with its problems per entry), in the guard on a harness save,
and in `kraft admin doctor`. A problem names the task, the list's source and
the entry's index: `chain 'default' task 'implementation.main.implementer':
fallback entry 0 (profile 'deep''s list): ...`. A disabled harness is not a
pairing problem, since the launch skips it.

The **candidates** are the task's own launch, then each entry in order. Kraft
moves to the next candidate, in the same dispatch, when:

- a launch ends **rate-limited** (a harness that declares `rate_limit_signal`
  reported a rejected request). The next launch is a fresh session in the same
  worktree, with the task's instruction and a note that the earlier attempt
  may have left partial work (`git status`, `git diff`);
- a candidate is **unavailable** before anything launches: its harness
  profile is missing, disabled or sets a default Kraft cannot apply, its agent
  profile is missing or has no model for its provider, or its executable is
  not on `PATH` (not checked under a sandbox, where the executable lives in the
  container). This is checked again at every launch, so fixing it takes effect
  at once;
- a candidate is **known to be limited**: Kraft remembers, from the event log,
  which harness and model a `rate_limit_hit` limited, on any work item, and
  skips that pair until its reset. One account-wide limit therefore costs one
  quickly-refused launch per model before each is remembered.

When no candidate is left and one was limited, the item parks as
`rate_limited` until the earliest reset among them, and its relaunch starts
from the top of the list, so the first choice is used again as soon as it is
back. When every candidate is unavailable, the task stops for a human with a
reason naming each one and why.

What carries over and what does not:

- The work item's `agent_overrides` and a node's `model`/`effort` override
  pick the task's own launch only, and so does a fix loop's escalation model.
  A fallback runs exactly as its entry says. A node's `extra_prompt` is part
  of the instruction and does reach it.
- Switching spends none of `rate_limit_retries`, which still counts parks. The
  spend caps in `budget:` are checked before every launch, fallbacks included,
  and a task's time cap covers all of its attempts together.
- Every entry's harness must be in the task's `allowed_harnesses`. A task's
  own list is checked when the chain materializes; a profile's list, which is
  live, is checked at the launch, which skips a refused entry as unavailable. `deny_tools`, `allowed_tools` and the item's
  sandbox apply to a fallback as to the task's own launch.

It is opt-in. A task with no list (none of its own, and none on its profile,
or `fallback: []`) launches, parks and stops exactly as it would without it,
and never consults the memory. No shipped task or profile declares one. A gate's `auto_review` task cannot declare a `fallback:` list of its own. A list it
inherits from its agent profile is ignored, because a gate review launches once
(tracked as Kraft-t4y8g).

Only a harness that declares `rate_limit_signal` can trigger a switch on a
rate limit, which today is `claude`. `codex` and `gemini` can be fallback
targets, and an unavailable one is skipped, but a rate limit on them fails
the launch as it does without a list.

Every skip or switch is logged:

- one `launch_fallback` event (`kraft view events --type launch_fallback`),
  with `from`, `to` (`null` when nothing was left), `reason`
  (`rate_limit_hit`, `known_limited` or `unavailable`, with a `detail`),
  `resets_at_iso` and `override_not_carried`;
- one sentence on the item's timeline, such as "Ran on claude / sonnet instead
  of claude / opus: claude / opus is rate-limited until 15:40.";
- one INFO line in the server log;
- a "fallback" marker on the board card while the item's latest launch is a
  fallback's, with the same sentence as its tooltip.

Session rows record the model that actually ran, so Analytics attributes each
attempt's cost to it.
