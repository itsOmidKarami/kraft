---
title: Agent harnesses
description: Which agent CLIs Kraft runs, what each supports, and how a task picks one.
---

A harness is one agent runtime described as data; this page lists the six Kraft ships and what each supports.

## In this section

- [How each harness runs unattended](/reference/harnesses/unattended-runs): the mode each CLI runs in when nobody can answer a prompt.
- [Agent profiles](/reference/harnesses/agent-profiles): named model tiers a task selects with `profile:`.
- [Harness files](/reference/harnesses/harness-files): the YAML file that describes one harness.
- [Fallback and escalation](/reference/harnesses/fallback-and-escalation): where a launch goes next, and which harness runs an escalation turn.

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent` task in [`library.yaml`](/reference/configuration/library-and-chains#libraryyaml-reusable-components)
names a harness *profile* in its `harness:` field, and `harnesses.yaml` says
which harness (the profile's `provider`) that profile runs:

```yaml
spec_author: { kind: agent, harness: codex, prompt: "...", produces: spec }
```

Kraft ships six harnesses:

| id | Binary | Notable gaps |
|---|---|---|
| `claude` | `claude` | Full capability set. |
| `codex` | `codex exec` | No `restrict_tools`, `approval_channel`, or `autocompact` — a profile or task asking for one of those is rejected at load. `deny_tools` and `allowed_tools` work through a `PreToolUse` hook passed with `-c` and trusted for that launch only, answered by the [permission gate](/reference/permissions#codex); web search never reaches it. Tokens, the thread id and a usage-limit stop are read off its `--json` log; it reports no cost, and no reset time for a limit. |
| `cursor` | `agent -p --trust` | Cursor's agent CLI. Runs in `--auto-review` (Cursor's classifier); `permission_mode: force` overrides it. Every launch gets a Kraft-owned config dir with commit attribution off ([Cursor](#cursor)). No out-of-band context channel (context goes in the prompt), no `effort` (a model id can carry one, such as `'name[effort=high]'`), and no `restrict_tools`, `approval_channel`, `autocompact` or `rate_limit_signal`. `deny_tools` and `allowed_tools` work through a `preToolUse` hook Kraft installs in the worktree, answered by the [permission gate](/reference/permissions#cursor). Tokens and the chat id `resume` takes are read off its `stream-json` log; it reports no cost. An API-key install needs `env_passthrough: [CURSOR_API_KEY]` on the repo. |
| `opencode` | `opencode run` | No out-of-band context channel (context goes in the prompt), no `restrict_tools`, `approval_channel` or `autocompact`. `deny_tools` and `allowed_tools` are written into the launch's own OpenCode config, with `--standalone`, when the task's policy sets either ([permission gate](/reference/permissions#opencode-and-amp-rules-written-at-launch)). `model` is `provider/model` for any provider OpenCode knows. There is no `effort`: OpenCode 2.x dropped `--variant`, so name a variant in the model id (`openai/gpt-5.5#high`). Every launch passes `--auto`, since `run` otherwise rejects every permission request. Tokens, cost, the session id and a rate-limit stop are read off its `--format json` log. In 2.x that log leaves out the last step's usage, so Kraft reads the session's totals from `opencode session export <session id>` when the run ends, and falls back to the log's steps if that fails. A `task` sub-agent's tokens are not in the log. |
| `gemini` | `gemini` | No out-of-band context channel (context goes in-band via the prompt), no `effort`, no `resume` at all (Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability isn't declared). |
| `amp` | `amp -x` | No `model`: Amp picks it. `effort` is Amp's mode (`-m low\|medium\|high\|ultra`). Context goes in-band via the prompt. No `permission_mode` (Amp asks for no approvals), no `restrict_tools`, `approval_channel`, `autocompact` or `rate_limit_signal`. `deny_tools` and `allowed_tools` go into a settings file of the launch's own (`--settings-file`) when the task's policy sets either ([permission gate](/reference/permissions#opencode-and-amp-rules-written-at-launch)). Tokens and the thread id `resume` takes are read off its `--stream-json` log; it reports no cost. Both command lines pass `--no-archive-after-execute`, because an archived thread can't be resumed. |

## Capabilities, not flags

A task's YAML never names a harness's actual CLI flags. It asks for a
**capability** — `prompt`, `context`, `model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `restrict_tools`, `approval_channel`, `resume`,
`autocompact`, `structured_log`, `usage`, `rate_limit_signal`, `writable_dirs` — and each harness's own YAML
(a YAML file per harness) maps that capability onto
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

Amp needs credentials a headless process can use: your `amp login`, or an
access token in `AMP_API_KEY` named in the repo's `env_passthrough`. See
[Set up Amp and Cursor credentials](/guides/adding-a-harness#set-up-amp-or-cursor-credentials).
With neither, `amp` prints a device-login prompt and waits about five minutes
for a browser before it exits 1.

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
- **Config directory.** Every launch sets `CURSOR_CONFIG_DIR` to
  `$KRAFT_HOME/run/harness-config/cursor/`, a directory Kraft owns. Your own
  `~/.cursor` is never read or changed. Before each launch Kraft writes
  `cli-config.json` there with commit attribution off, because with it on
  Cursor adds a `Co-authored-by: Cursor` trailer to every commit and the
  classifier refused those commits. The file adds no permission rule. The
  directory is shared by all launches, not one per launch, because `--resume`
  has to find the chat an earlier launch wrote.
- **Tool policy.** `deny_tools` and `allowed_tools` have no Cursor flag. When a
  task's policy has something to enforce, Kraft writes a `preToolUse` entry
  into the worktree's `.cursor/hooks.json` that asks its
  [permission gate](/reference/permissions#cursor)
  about every tool call, and keeps that file out of the branch's commits. A
  hook deny blocks the call. A hook allow does not override `--auto-review`,
  so a granted call can still be refused by Cursor's classifier.
- **Login.** The login lives in the OS keychain, not the config dir. With an
  API key instead, name `CURSOR_API_KEY` in the repo's `env_passthrough`.
- **Usage.** Tokens come off the log's closing `result` line, one per run:
  uncached input, output, and cache reads and writes. Cursor reports no cost,
  so Kraft records none.
