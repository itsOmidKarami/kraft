---
title: Agent harnesses
navigation:
  title: Overview
description: Which agent CLIs Kraft runs, what each supports, and how a task picks one.
---

A harness is one agent runtime described as data; this page lists the seven Kraft ships and what each supports.

## In this section

- [How each harness runs unattended](/reference/harnesses/unattended-runs): the mode each CLI runs in when nobody can answer a prompt.
- [Agent profiles](/reference/harnesses/agent-profiles): named model tiers a task selects with `profile:`.
- [Harness files](/reference/harnesses/harness-files): the YAML file that describes one harness.
- [Fallback and escalation](/reference/harnesses/fallback-and-escalation): where a launch goes next, and which harness runs an escalation turn.
- [Cost reporting](/reference/harnesses/cost-reporting): how Kraft estimates the cost of a harness that reports tokens but no dollar figure.

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent` task in [`library.yaml`](/reference/configuration/library-and-chains#libraryyaml-reusable-components)
names a harness *profile* in its `harness:` field, and `harnesses.yaml` says
which harness (the profile's `provider`) that profile runs:

```yaml
spec_author: { kind: agent, harness: claude, prompt: "...", produces: spec }
```

Every agent task Kraft ships names `claude`. To run a task on another
harness, make sure `harnesses.yaml` has a profile for it (the shipped file has
`claude` and `codex`) and change the task's `harness:` to that profile's id.
[Switch a task to another harness](/guides/harnesses/switch-harness) walks through it.

Kraft ships seven harnesses:

| id | Binary | Notable gaps |
|---|---|---|
| `claude` | `claude` | Every capability but `writable_dirs`, which only Codex declares. |
| `codex` | `codex exec` | No `restrict_tools`, `approval_channel`, or `autocompact` — a profile or task asking for one of those is rejected at load. See [Codex details](#codex-details). |
| `cursor` | `agent -p --trust` | Cursor's agent CLI. Runs in `--auto-review` (Cursor's classifier); `permission_mode: force` overrides it. See [Cursor details](#cursor-details). |
| `opencode` | `opencode run` | Needs OpenCode 2.0.0 or newer (not npm's 1.x `opencode-ai`); an older one is refused at launch. See [OpenCode details](#opencode-details). |
| `antigravity` | `agy -p` | Google's Antigravity CLI, for an individual Google account: Gemini CLI stopped serving those on 2026-06-18, so `gemini` is for API-key and Code Assist users. See [Antigravity details](#antigravity-details). |
| `gemini` | `gemini` | By default, each launch passes `--approval-mode yolo`, and no call reaches the [permission gate](/reference/permissions). See [Gemini details](#gemini-details). |
| `amp` | `amp -x` | No `model`: Amp picks it. `effort` is Amp's mode (`-m low\|medium\|high\|ultra`). See [Amp details](#amp-details). |

## The harnesses in detail

Each table lists what the overview row leaves out for that harness.

### Codex details

| Topic | What to know |
|---|---|
| Missing | No `restrict_tools`, `approval_channel`, or `autocompact` — a profile or task asking for one of those is rejected at load. |
| Tool policy | `deny_tools` and `allowed_tools` work through a `PreToolUse` hook passed with `-c` and trusted for that launch only, answered by the [permission gate](/reference/permissions#codex); web search never reaches it. |
| Read from its log | Tokens, the thread id and a usage-limit stop are read off its `--json` log. |
| Cost | It reports no cost, and no reset time for a limit, so [the dollar caps estimate it](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost) on the model Kraft launched it with. |

### Cursor details

| Topic | What to know |
|---|---|
| Launch | Cursor's agent CLI. Runs in `--auto-review` (Cursor's classifier); `permission_mode: force` overrides it. |
| Effort | No `effort` (a model id can carry one, such as `'name[effort=high]'`). |
| Missing | No out-of-band context channel (context goes in the prompt), and no `restrict_tools`, `approval_channel`, `autocompact` or `rate_limit_signal`. |
| Tool policy | `deny_tools` and `allowed_tools` work through a `preToolUse` hook Kraft installs in the worktree, answered by the [permission gate](/reference/permissions#cursor). |
| Read from its log | Tokens and the chat id `resume` takes are read off its `stream-json` log. |
| Cost | It reports no cost and names its model "Auto", so Kraft [estimates it](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost) only on a launch model `prices.json` lists. Otherwise the item and daily dollar caps count it as $0 and Kraft warns. |
| Needs | An API-key install needs `env_passthrough: [CURSOR_API_KEY]` on the repo. |

### OpenCode details

| Topic | What to know |
|---|---|
| Needs | OpenCode 2.0.0 or newer (not npm's 1.x `opencode-ai`); an older one is refused at launch. |
| Launch | Every launch passes `--auto`, since `run` otherwise rejects every permission request. |
| Model | `model` is `provider/model` for any provider OpenCode knows. |
| Effort | There is no `effort`: name a variant in the model id (`openai/gpt-5.5#high`). |
| Missing | No out-of-band context channel (context goes in the prompt), no `restrict_tools`, `approval_channel` or `autocompact`. |
| Tool policy | `deny_tools` and `allowed_tools` are written into the launch's own OpenCode config, with `--standalone`, when the task's policy sets either ([permission gate](/reference/permissions#opencode-and-amp-rules-written-at-launch)). |
| Read from its log | Tokens, cost, the session id and a rate-limit stop are read off its `--format json` log. |
| The last step's usage | That log leaves out the last step's usage, so Kraft reads the session's totals from `opencode session export <session id>` when the run ends, and falls back to the log's steps if that fails. |
| Sub-agents | A `task` sub-agent's tokens are not in the log. |

### Antigravity details

| Topic | What to know |
|---|---|
| Launch | Google's Antigravity CLI, for an individual Google account: Gemini CLI stopped serving those on 2026-06-18, so `gemini` is for API-key and Code Assist users. |
| Permissions | Every launch passes `--dangerously-skip-permissions`, since headless `agy` otherwise denies every file write and shell command and still exits 0. |
| Control | Because every launch passes that flag, Kraft has no per-action control over an `agy` worker: the worktree is the boundary, and a [sandbox](/reference/configuration/repos#sandboxed-workers) is the way to bound what it can reach. |
| Effort | `effort` is `--effort low\|medium\|high\|max`, checked by `agy` against the model. |
| Model | Name the base model (`gemini-3.8-flash`) and set `effort`, not a slug with the effort in it (`gemini-3.8-flash-low`), or the session can't be priced. |
| Missing | No out-of-band context channel (context goes in the prompt), no `deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel` or `autocompact`: a task with a tool policy is refused. |
| Read from its log | Tokens, the conversation id `resume` takes and a quota stop are read off its `stream-json` log. |
| Cost | It reports no cost, so [the dollar caps estimate it](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost) on the model it was launched with when `prices.json` lists it. |
| Needs | A prior interactive sign-in (`agy` once) on the machine, or a Gemini API key: see [Antigravity credentials](/guides/harnesses/adding-a-harness#antigravity). |

### Gemini details

| Topic | What to know |
|---|---|
| Launch | By default, each launch passes `--approval-mode yolo`, and no call reaches the [permission gate](/reference/permissions). |
| Missing | No out-of-band context channel (context goes in-band via the prompt), no `effort`, no `deny_tools` or `allowed_tools` (a task with a tool policy is refused). |
| Resume | No `resume` at all (Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability isn't declared). |

### Amp details

| Topic | What to know |
|---|---|
| Model and effort | No `model`: Amp picks it. `effort` is Amp's mode (`-m low\|medium\|high\|ultra`). |
| Missing | Context goes in-band via the prompt. No `permission_mode` (Amp asks for no approvals), no `restrict_tools`, `approval_channel`, `autocompact` or `rate_limit_signal`. |
| Tool policy | `deny_tools` and `allowed_tools` go into a settings file of the launch's own (`--settings-file`) when the task's policy sets either ([permission gate](/reference/permissions#opencode-and-amp-rules-written-at-launch)). |
| Read from its log | Tokens and the thread id `resume` takes are read off its `--stream-json` log. |
| Cost | It reports no cost, so [the dollar caps estimate it](/reference/harnesses/cost-reporting#harnesses-that-report-no-cost) on the model its log names when `prices.json` lists it. |
| Launch | Both command lines pass `--no-archive-after-execute`, because an archived thread can't be resumed. |

## Capabilities, not flags

A task's YAML never names a harness's actual CLI flags. It asks for a
**capability** — `prompt`, `context`, `model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `resume`,
`autocompact`, `structured_log`, `usage`, `rate_limit_signal`, `writable_dirs`, `mcp_config` — and each harness's own YAML
(a YAML file per harness) maps that capability onto
whatever its CLI actually calls it. `permission_mode` is `--permission-mode
acceptEdits|auto|...` for Claude, `-c sandbox_mode=read-only|workspace-write|...`
for Codex, `--approval-mode default|yolo|...` for Gemini, `--auto-review|--force`
for Cursor — one Kraft-side name, four different flags.

Codex's context, effort, permission mode and writable-roots options are `-c` config keys.

- Codex runs in its "approve for me" mode by default, Claude's `auto` counterpart: the
  sandbox is `workspace-write`, and a sandbox escalation the model asks for goes
  to Codex's automatic reviewer (`approval_policy=on-request`,
  `approvals_reviewer=auto_review`), not to a human.
- A `permission_mode` of
  `read-only` or `danger-full-access` (a harness profile's `defaults:` or a task)
  changes the sandbox. The reviewer stays on in every mode.
- Under Kraft's
  [docker sandbox](/reference/configuration/repos#sandboxed-workers) the default
  becomes `danger-full-access` (`container_permission_mode`): Codex's own
  sandbox cannot start inside a container, and the container is the boundary.

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
own sandbox would refuse to write outside the worktree.

Codex binds it to
`-c sandbox_workspace_write.writable_roots={value}` (TOML reads the JSON array
as an inline array). Its `workspace-write` sandbox writes only the workspace
and `/tmp`, so without the grant a codex worker on a default install
(`~/.kraft`) can write neither its result file nor a commit. Kraft grants both
directories outright, because Codex's automatic reviewer is not relied on for
either one.

A harness
that doesn't declare `writable_dirs` gets nothing extra.

Another is filled by Kraft only for a [sandboxed](/reference/configuration/repos#sandboxed-workers)
launch with `network:`: `mcp_config`, the CLI's MCP servers as one JSON object,
`{"mcpServers": {"kraft": {"type": "http", "url": "http://kraft/mcp"}}}`: Kraft's
own server for that session, reached through the sandbox's route out, since the
MCP server registered on your machine is out of the container's reach. Claude
binds it to `--strict-mcp-config --mcp-config {value}`, so that server is its
only one and its `approval_channel` tool is answered there.

Some harnesses declare `values:` on a capability — a closed vocabulary the
CLI itself would reject (Codex's `effort` is `minimal, low, medium, high, xhigh`,
Claude's is `low, medium, high, xhigh, max`) — checked at load time, and
`always:` — the value Kraft uses when nothing else is supplied (Gemini's
`permission_mode` defaults to `yolo`, since a headless worker has nobody to
answer an approval prompt).

### Amp

Amp needs credentials a headless process can use. See [Set up harness credentials](/guides/harnesses/adding-a-harness#set-up-harness-credentials).

- Kraft's token counts for an Amp run are the thread's own, message by message.
  They match `amp threads export`.
- Amp's bill (`amp threads usage`) can count a few requests that aren't in the
  thread, and it's the only place Amp reports cost, so Kraft records none.
- An agent profile can't select `amp`: a profile needs a model for the
  provider, and Amp takes none. A task on `amp` sets `effort:` itself.

### Cursor

- **Mode.** Kraft runs `agent` in `--auto-review`, Cursor's Smart Auto: a
  server-side classifier runs the tool calls it judges safe and refuses the
  rest. Without a mode, print mode only proposes edits and applies none.
- **Config directory.**
  - Every launch sets `CURSOR_CONFIG_DIR` to
    `$KRAFT_HOME/run/harness-config/cursor/`, a directory Kraft owns. Your own
    `~/.cursor` is never read or changed.
  - Before each launch Kraft writes
    `cli-config.json` there with commit attribution off, because with it on
    Cursor adds a `Co-authored-by: Cursor` trailer to every commit and the
    classifier refused those commits. The file adds no permission rule.
  - The
    directory is shared by all launches, not one per launch, because `--resume`
    has to find the chat an earlier launch wrote. A sandboxed item gets one of
    its own, inside its sandbox home.
- **Tool policy.** `deny_tools` and `allowed_tools` go through a `preToolUse` hook. See [Cursor](/reference/permissions#cursor).
- **Login.** The login lives in the OS keychain, not the config dir. With an
  API key instead, name `CURSOR_API_KEY` in the repo's `env_passthrough`: the
  only way a [sandboxed](/reference/configuration/repos#sandboxed-workers)
  worker, which has no keychain, logs in.
- **Usage.** Tokens come off the log's closing `result` line, one per run:
  uncached input, output, and cache reads and writes. Cursor reports no cost,
  so Kraft records none, only an estimate when it can price the launch model.
