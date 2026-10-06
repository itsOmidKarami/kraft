---
title: Agent harnesses
navigation:
  title: Overview
description: Which agent CLIs Kraft runs, what each supports, and how a task picks one.
---

A **harness** is one agent runtime, described as data: a fact about a CLI, not code. This page compares the seven Kraft ships.

## How a task picks a harness

An `agent` task in [`library.yaml`](/reference/configuration/library-and-chains#libraryyaml-reusable-components) names a **harness profile** in its `harness:` field. `harnesses.yaml` says which harness, the profile's `provider`, that profile runs:

```yaml [config/library.yaml]
spec_author: { kind: agent, harness: claude, prompt: "...", produces: spec }
```

Every agent task Kraft ships names `claude`. To run a task on another harness, follow [Switch a task to another harness](/guides/harnesses/switch-harness).

## Compare the harnesses

In the Resume and Rate-limit fallback columns, a tick means the harness declares the capability and a dash means it does not.

| Harness | Command | Model, effort | Tool lists | Cost | Context | Resume | Rate-limit fallback | API-key variable |
|---|---|---|---|---|---|---|---|---|
| `claude` | `claude` | both | Gate | Yes | System prompt | ✓ | ✓ | `ANTHROPIC_API_KEY` |
| `codex` | `codex exec` | both | Gate | Tokens | System prompt | ✓ | ✓ | `CODEX_API_KEY` |
| `cursor` | `agent -p --trust` | model | Gate | Tokens | Prompt | ✓ | – | `CURSOR_API_KEY` |
| `opencode` | `opencode run` | model | Rules | Yes | Prompt | ✓ | ✓ | the provider's own key |
| `antigravity` | `agy -p` | both | Refused | Tokens | Prompt | ✓ | ✓ | none: sign-in (`agy`), or `GEMINI_API_KEY` |
| `gemini` | `gemini` | model | Refused | Agent-written | Prompt | – | – | `GEMINI_API_KEY` |
| `amp` | `amp -x` | effort | Rules | Tokens | Prompt | ✓ | – | `AMP_API_KEY` |

What the columns mean:

- **Model, effort**: which of `model` and `effort` the harness takes. `amp` takes no `model`, because Amp picks it, and its `effort` is Amp's mode. `cursor`, `opencode` and `gemini` take no `effort`.
- **Tool lists**: how Kraft enforces `deny_tools` and `allowed_tools` on an unattended worker. **Gate**: the [permission gate](/reference/permissions) answers each call. **Rules**: the CLI enforces rules Kraft wrote for the launch. **Refused**: a task with a tool list does not launch. See [Unattended runs](/reference/harnesses/unattended-runs).
- **Cost**: **Yes** means the harness reports a dollar cost. **Tokens** means it reports tokens only, and Kraft estimates the cost. **Agent-written** means the agent writes its own counts into the result file. See [Cost reporting](/reference/harnesses/cost-reporting).
- **Context**: how Kraft passes its context to the agent. **System prompt** is out of band. **Prompt** is in band, folded into the task's prompt.
- **Rate-limit fallback**: a rate limit on this harness can trigger a switch to the next entry of a [`fallback:` list](/reference/harnesses/fallback-and-escalation).
- **API-key variable**: the variable that carries an API key to a worker. A worker can use the CLI's own stored login instead where it has one. [Run workers on your agent](/guides/day-to-day/agent-integration#run-workers-on-your-agent) has each login.

Only `claude` declares `restrict_tools`, `approval_channel`, `autocompact` and `mcp_config`, and only `codex` declares `writable_dirs`. A task or profile that sets an option whose capability its harness lacks is rejected at load. [Harness files](/reference/harnesses/harness-files#capabilities) lists every capability.

## The harnesses in detail

Each table lists what the comparison leaves out for that harness.

### Codex details

| Topic | What to know |
|---|---|
| Config keys | Its context, effort, permission mode and writable-roots options are `-c` config keys. |
| Tool lists | A `PreToolUse` hook passed with `-c` and trusted for that launch only. Web search never reaches it. See [Codex](/reference/permissions#codex). |
| Read from its log | Tokens, the thread id and a usage-limit stop, off its `--json` log. The log gives no reset time for a limit. |

### Cursor details

| Topic | What to know |
|---|---|
| Effort | No `effort`. A model id can carry one, such as `'name[effort=high]'`. |
| Tool lists | A `preToolUse` hook Kraft installs in the worktree. See [Cursor](/reference/permissions#cursor). |
| Read from its log | Tokens and the chat id `resume` takes, off its `stream-json` log. Tokens come off the closing `result` line, one per run: uncached input, output, and cache reads and writes. |
| Config directory | Every launch sets `CURSOR_CONFIG_DIR` to `$KRAFT_HOME/run/harness-config/cursor/`, a directory Kraft owns. Your own `~/.cursor` is never read or changed. |
| Commit attribution | Before each launch Kraft writes `cli-config.json` there with commit attribution off, because with it on Cursor adds a `Co-authored-by: Cursor` trailer to every commit and the classifier refused those commits. The file adds no permission rule. |
| Shared directory | All launches share the directory, not one per launch, because `--resume` has to find the chat an earlier launch wrote. A sandboxed item gets one of its own, inside its sandbox home. |
| Sign-in | The login lives in the OS keychain, not the config directory. A [sandboxed](/reference/configuration/sandbox) worker has no keychain, so it needs `CURSOR_API_KEY`, named in the repo's `env_passthrough`. |

### OpenCode details

| Topic | What to know |
|---|---|
| Needs | OpenCode 2.0.0 or newer, not npm's 1.x `opencode-ai`. Kraft refuses an older one at launch. See [`min_version`](/reference/harnesses/harness-files#min_version). |
| Launch | Every launch passes `--auto`, since `run` otherwise rejects every permission request. |
| Model | `model` is `provider/model` for any provider OpenCode knows. |
| Effort | There is no `effort`. Name a variant in the model id (`openai/gpt-5.5#high`). |
| Tool lists | Written into the launch's own OpenCode config, with `--standalone`, when the task's policy sets either. See [OpenCode and Amp](/reference/permissions#opencode-and-amp-rules-written-at-launch). |
| Read from its log | Tokens, cost, the session id and a rate-limit stop, off its `--format json` log. |
| The last step's usage | That log leaves out the last step's usage, so Kraft reads the session's totals from `opencode session export <session id>` when the run ends. It falls back to the log's steps if that fails. |
| Sub-agents | A `task` sub-agent's tokens are not in the log. |

### Antigravity details

| Topic | What to know |
|---|---|
| Accounts | Google's Antigravity CLI, for an individual Google account. `gemini` no longer serves those: see [Gemini details](#gemini-details). |
| Control | Kraft has no per-action control over an `agy` worker. See [Unattended runs](/reference/harnesses/unattended-runs#notes-by-harness). |
| Effort | `effort` is `--effort low\|medium\|high\|max`, checked by `agy` against the model. |
| Model | Name the base model (`gemini-3.8-flash`) and set `effort`, not a slug with the effort in it (`gemini-3.8-flash-low`), or Kraft cannot price the session. |
| Read from its log | Tokens, the conversation id `resume` takes and a quota stop, off its `stream-json` log. |
| Needs | A prior interactive sign-in (`agy` once) on the machine, or a Gemini API key. See [Antigravity credentials](/guides/harnesses/adding-a-harness#antigravity). |

### Gemini details

| Topic | What to know |
|---|---|
| Accounts | Gemini CLI stopped serving free, Google AI Pro and Ultra accounts on 2026-06-18. `gemini` works with a Gemini API key or Gemini Code Assist. For Gemini models on an individual Google account, use `antigravity`. |
| Resume | No `resume` at all. Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability is not declared. |

### Amp details

| Topic | What to know |
|---|---|
| Model and effort | No `model`: Amp picks it. `effort` is Amp's mode (`-m low\|medium\|high\|ultra`). An agent profile cannot select `amp`, because a profile needs a model for the provider. A task on `amp` sets `effort:` itself. |
| Tool lists | A settings file of the launch's own (`--settings-file`), when the task's policy sets either. See [OpenCode and Amp](/reference/permissions#opencode-and-amp-rules-written-at-launch). |
| Launch | Both command lines pass `--no-archive-after-execute`, because an archived thread cannot be resumed. |
| Read from its log | Tokens and the thread id `resume` takes, off its `--stream-json` log. The token counts are the thread's own, message by message, and match `amp threads export`. |
| Cost | Amp's bill (`amp threads usage`) can count a few requests that are not in the thread. It is the only place Amp reports cost, so Kraft records none. |
| Needs | Credentials a headless process can use. See [Amp credentials](/guides/harnesses/adding-a-harness#amp). |

## In this section

- [Unattended runs](/reference/harnesses/unattended-runs): the mode each CLI runs in when nobody can answer a prompt.
- [Agent profiles](/reference/harnesses/agent-profiles): named model tiers a task selects with `profile:`.
- [Harness files](/reference/harnesses/harness-files): the YAML file that describes one harness, and every capability it can declare.
- [Fallback and escalation](/reference/harnesses/fallback-and-escalation): where a launch goes next, and which harness runs an escalation turn.
- [Cost reporting](/reference/harnesses/cost-reporting): how Kraft estimates the cost of a harness that reports tokens but no dollar figure.
