# Agent harnesses

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent` task in [`library.yaml`](configuration.md#libraryyaml-reusable-components)
names a harness *profile* in its `harness:` field, and `harnesses.yaml` says
which harness (the profile's `provider`) that profile runs:

```yaml
spec_author: { kind: agent, harness: codex_default, prompt: "...", produces: spec }
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
