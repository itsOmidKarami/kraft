# Agent harnesses

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent` task in [`library.yaml`](configuration.md#libraryyaml-reusable-components)
names a harness *profile* in its `harness:` field, and `harnesses.yaml` says
which harness (the profile's `provider`) that profile runs:

```yaml
spec_author: { kind: agent, harness: codex, prompt: "...", produces: spec }
```

Kraft ships six harnesses:

| id | Binary | Notable gaps |
|---|---|---|
| `claude` | `claude` | Full capability set. |
| `codex` | `codex exec` | No `deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, or `autocompact` — a profile or task asking for one of those is rejected at load. Tokens, the thread id and a usage-limit stop are read off its `--json` log; it reports no cost, and no reset time for a limit. |
| `cursor` | `agent -p --trust` | Cursor's agent CLI. Runs in `--auto-review` (Cursor's classifier); `permission_mode: force` overrides it. Every launch gets a Kraft-owned config dir with commit attribution off ([Cursor](#cursor)). No out-of-band context channel (context goes in the prompt), no `effort` (a model id can carry one, such as `'name[effort=high]'`), and no `restrict_tools`, `approval_channel`, `autocompact` or `rate_limit_signal`. `deny_tools` and `allowed_tools` work through a `preToolUse` hook Kraft installs in the worktree, answered by the [permission gate](permissions.md#cursor-a-hook-before-every-call). Tokens and the chat id `resume` takes are read off its `stream-json` log; it reports no cost. An API-key install needs `env_passthrough: [CURSOR_API_KEY]` on the repo. Checked with real runs on cursor-agent 2026.09.18-9a7762b. |
| `opencode` | `opencode run` | No out-of-band context channel (context goes in the prompt), no `restrict_tools`, `approval_channel` or `autocompact`. `deny_tools` and `allowed_tools` are written into the launch's own OpenCode config, with `--standalone`, when the task's policy sets either ([permission gate](permissions.md#opencode-and-amp-rules-written-at-launch)). `model` is `provider/model` for any provider OpenCode knows. There is no `effort`: OpenCode 2.x dropped `--variant`, so name a variant in the model id (`openai/gpt-5.5#high`). Every launch passes `--auto`, since `run` otherwise rejects every permission request. Tokens, cost, the session id and a rate-limit stop are read off its `--format json` log. In 2.x that log leaves out the last step's usage, so Kraft reads the session's totals from `opencode session export <session id>` when the run ends, and falls back to the log's steps if that fails (Kraft-ihoen). A `task` sub-agent's tokens are not in the log. Checked against opencode 2.0.15. |
| `gemini` | `gemini` | No out-of-band context channel (context goes in-band via the prompt), no `effort`, no `resume` at all (Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability isn't declared). |
| `amp` | `amp -x` | No `model`: Amp picks it. `effort` is Amp's mode (`-m low\|medium\|high\|ultra`). Context goes in-band via the prompt. No `permission_mode` (Amp asks for no approvals), no tool lists, no `approval_channel`, `autocompact` or `rate_limit_signal`. Tokens and the thread id `resume` takes are read off its `--stream-json` log; it reports no cost. Both command lines pass `--no-archive-after-execute`, because an archived thread can't be resumed. Checked with real Kraft work items on amp 0.0.1790142911. |

## Capabilities, not flags

A task's YAML never names a harness's actual CLI flags. It asks for a
**capability** — `prompt`, `context`, `model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `resume`,
`autocompact`, `structured_log`, `usage`, `rate_limit_signal`, `writable_dirs` — and each harness's own YAML
(`src/kraft/harnesses/*.yaml` in the package) maps that capability onto
whatever its CLI actually calls it. `permission_mode` is `--permission-mode
acceptEdits|auto|...` for Claude, `-c sandbox_mode=read-only|workspace-write|...`
for Codex, `--approval-mode default|yolo|...` for Gemini, `--auto-review|--force`
for Cursor — one Kraft-side name, four different flags.

Codex's options are all `-c` config keys, because `codex exec resume` rejects
`-s`, `--add-dir` and `--approve-for-me` after `resume` and accepts `-c`. Codex
runs in its "approve for me" mode by default, Claude's `auto` counterpart: the
sandbox is `workspace-write`, and a sandbox escalation the model asks for goes
to Codex's automatic reviewer (`approval_policy=on-request`,
`approvals_reviewer=auto_review`), not to a human. A `permission_mode` of
`read-only` or `danger-full-access` (a harness profile's `defaults:` or a task)
changes the sandbox. The reviewer stays on in every mode.

Three capabilities are required — `prompt`, `context`, `usage` — since no
agent dispatch can be built without them. Two are non-invocable —`usage`,
`rate_limit_signal` — they describe what Kraft reads back out of a session
(from its structured log or a result file), not an argv it constructs.

One is filled by Kraft, never by a task: `writable_dirs`, the directories
outside the worktree that a worker must write. There are two:

- the directory holding the launch's `$KRAFT_RESULT_PATH`
  (`$KRAFT_HOME/run/results`);
- the worktree's git common dir, where every commit writes. For a linked
  worktree that's the main checkout's `.git`. Kraft asks git for it
  (`git rev-parse --git-common-dir`) and leaves it out when git has none.

`{value}` is one JSON array of absolute paths, such as
`["/home/me/.kraft/run/results","/home/me/src/app/.git"]`. It's for a CLI whose
own sandbox would refuse to write outside the worktree. Codex binds it to
`-c sandbox_workspace_write.writable_roots={value}` (TOML reads the JSON array
as an inline array). Its `workspace-write` sandbox writes only the workspace
and `/tmp`, so without the grant a codex worker on a default install
(`~/.kraft`) can write neither its result file nor a commit. The automatic
reviewer can't be relied on for either one. It approves only what the model
asks for, and it has declined a commit because the repo's `AGENTS.md` asked for
"clear authority" first. So Kraft grants both directories outright. A harness
that doesn't declare `writable_dirs` gets nothing extra.

Some harnesses declare `values:` on a capability — a closed vocabulary the
CLI itself would reject (Codex's `effort` is `minimal, low, medium, high`,
Claude's is `low, medium, high, xhigh, max`) — checked at load time, and
`always:` — a value Kraft forces regardless of what a binding asks for
(Gemini's `permission_mode` is always `yolo`: the disposable worktree is the
real safety boundary, not the approval mode, and a headless worker has nobody
to answer an approval prompt anyway).

### Amp

`amp` needs credentials a headless process can use. On a machine where you
ran `amp login`, that's already true: the login lives under your home
directory, which a worker keeps. Elsewhere, use an access token (`sgamp_...`,
from ampcode.com/settings) in `AMP_API_KEY`. A worker's environment is an
allowlist, so name it in the repo's `env_passthrough`. With neither, `amp`
doesn't fail fast. It prints a device-login prompt and waits about five
minutes for a browser before it exits 1.

Kraft's token counts for an Amp run are the thread's own, message by message
(they match `amp threads export`). Amp's bill (`amp threads usage`) can count
a few more requests that aren't in the thread, and it's the only place Amp
reports cost, so Kraft records none.

An agent profile can't select `amp`: a profile needs a model for the provider,
and Amp takes none. A task on `amp` sets `effort:` itself.

### Cursor

Kraft runs `agent` in `--auto-review`, Cursor's Smart Auto: a server-side
classifier runs the tool calls it judges safe and refuses the rest. Without a
mode, print mode only proposes edits and applies none.

Every cursor launch gets `CURSOR_CONFIG_DIR` pointing at
`$KRAFT_HOME/run/harness-config/cursor/`, a directory Kraft owns. Your own
`~/.cursor` is never read or changed. Before each launch Kraft writes
`cli-config.json` there: the file Cursor itself creates in an empty config
dir, with one change, commit attribution off. With attribution on, Cursor adds
a `Co-authored-by: Cursor` trailer to every commit the agent makes, and the
classifier refused those commits. With it off, a worker commits normally. The
file adds no permission rule. Cursor keeps its chats in the same directory,
which is why it is one directory per harness rather than one per launch:
`--resume` has to find the chat an earlier launch wrote.

`deny_tools` and `allowed_tools` have no Cursor flag. When a task's policy has
something to enforce, Kraft writes a `preToolUse` entry into the worktree's
`.cursor/hooks.json` that asks its [permission gate](permissions.md#cursor-a-hook-before-every-call)
about every tool call, and keeps that file out of the branch's commits. A hook
deny blocks the call; a hook allow does not override `--auto-review`, so a
granted call can still be refused by Cursor's classifier (Kraft-4in7z.6).

The login lives in the OS keychain, not the config dir, so a machine where you
ran `agent login` needs nothing else. With an API key instead, name
`CURSOR_API_KEY` in the repo's `env_passthrough`.

Tokens come off the log's closing `result` line, one per run: uncached input,
output and cache reads and writes. Cursor reports no cost, so Kraft records
none.

## How each harness runs unattended

A worker has nobody to answer a permission prompt. So Kraft runs each CLI in
its classifier mode where it has one, and otherwise in its most autonomous
unattended mode. What Kraft itself needs from a worker, its result file and
its commits, is granted explicitly and never left to a classifier.

| Harness | Mode | Classifier? | Notes |
|---|---|---|---|
| claude | `--permission-mode auto`, plus Kraft's MCP permission tool for what the classifier won't settle | yes | |
| codex | approve-for-me (-c keys), plus explicit results and `.git` writable roots | yes | |
| cursor | `--auto-review`, plus a per-launch config with commit attribution off, and Kraft's `preToolUse` hook when the task's policy has something to enforce | yes | the hook's deny blocks; its allow doesn't outrank the classifier |
| opencode | `--auto`, plus the task's tool policy as per-launch config rules (`--standalone`) when it has any | none in the CLI | no permission gate: the rules are enforced by OpenCode, and nothing is logged |
| amp | approves by default | no | |
| gemini | `--approval-mode yolo` | no | |

Claude sends the asks its classifier won't settle to Kraft, and Cursor's hook
asks Kraft before every call when policy has something to enforce. Routing the
other harnesses' calls to Kraft the same way is planned (epic Kraft-4in7z). See
[The permission gate](permissions.md) for how those asks reach Kraft and how
Kraft decides them.

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

## Escalation turns

An escalation turn, the conversation Kraft opens when a person escalates a
stopped item or `auto_escalate_stuck` does, runs on the `harnesses.yaml`
profile policy's `escalation_harness` names: `claude` unless you set it. Set
it in `policy.yaml`'s `defaults:`, on a repository, chain or node `policy:`,
or for one item with `kraft item set-policy --policy escalation_harness=codex`.
`item` follows the harness the item's own latest agent task ran on. The
thread id the next turn resumes is read with that harness's own log reader,
and a resumed turn records only what it added to the thread. See
[`escalation_harness`](configuration.md). A gate's automated review is not
this: its `auto_review` task names its own `harness:` in the chain.

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
`autocompact`, `structured_log`, `rate_limit_signal`, `writable_dirs`) is optional: omit what the CLI can't
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
[codex, exec, resume, "{value}"]`). `deny_tools` and `allowed_tools` can bind
`via: permission_hook` instead: no flag at all, the tool lists enforced by
Kraft's pre-tool hook. That needs a top-level `permission_hook:` naming the
translator that answers the CLI's hook (only `cursor` exists), and
`tool_names:` maps the CLI's own tool names to Kraft's (`Shell: Bash`), so a
call is checked under the name policy uses. Or they can bind `via:
permission_rules`, for a CLI with no hook: a top-level `permission_rules:`
names the renderer (`opencode`) that writes the tool lists into the CLI's own
permission config at launch, and `tool_names:` maps the other way round too,
so a policy name no CLI tool maps to refuses the launch.

A launch whose policy sets `allowed_tools` (an empty list included) must not
let a tool outside the list run, and pre-approving the listed ones is not
that. It needs two things from its harness: `restrict_tools`, the CLI's own
flag for which built-in tools exist (Claude's `--tools`, given the built-in
names from the list), and a `permission_mode` with an `under_allowlist:` mode
that asks the approval channel about everything else instead of approving it
(Claude's `manual`; its `auto` lets a classifier approve an unlisted tool
without asking). A harness missing either, `permission_mode` included,
refuses to launch under an allowlist, and so does a launch whose own
`permission_mode` differs from that mode. A harness with a `permission_hook`
needs neither: its hook, fail-closed for a session under an allowlist, denies
every tool the list doesn't name. Nor does one with `permission_rules`: the
rules it writes deny every tool the list doesn't name.

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
and never consults the memory. No shipped task or profile declares one. A gate's `auto_review` task cannot declare a `fallback:` list of its own, and is
refused the same way — at its own launch, naming the gate and the profile —
when its `profile:` carries one: a gate review launches once and never walks
either list.

Only a harness that declares `rate_limit_signal` can trigger a switch on a
rate limit, which today is `claude`, `codex` and `opencode`. `gemini`, `amp`
and `cursor` can be fallback targets, and are skipped when unavailable, but a
rate limit on them fails the launch as it does without a list.

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
