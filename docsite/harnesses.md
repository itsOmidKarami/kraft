# Agent harnesses

A **harness** is one agent runtime, described as data — a fact about a CLI, not
code. An `agent`-kind hook in [`registry.yaml`](configuration.md#registryyaml-hook-point-bindings)
names one by id in its `harness:` field:

```yaml
on.spec.requested: { kind: agent, harness: claude, skill: spec, artifact: spec }
```

`harness` defaults to `claude` when omitted. Kraft ships three:

| id | Binary | Notable gaps |
|---|---|---|
| `claude` | `claude` | Full capability set. |
| `codex` | `codex exec` | No `deny_tools`, `allowed_tools`, `approval_channel`, `autocompact`, or `rate_limit_signal` — a binding naming one of those is rejected at load. |
| `gemini` | `gemini` | No out-of-band context channel (context goes in-band via the prompt), no `effort`, no `resume` at all (Gemini's `--resume` takes an index or `"latest"`, not a session id, so the capability isn't declared). |

## Capabilities, not flags

A hook's YAML never names a harness's actual CLI flags. It asks for a
**capability** — `prompt`, `context`, `model`, `effort`, `permission_mode`,
`deny_tools`, `allowed_tools`, `approval_channel`, `resume`, `autocompact`,
`structured_log`, `usage`, `rate_limit_signal` — and each harness's own YAML
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

`kraft admin doctor` runs one PATH check per harness the live registry
actually names (not every bundled one — an install with no `codex` binding
anywhere isn't told to go install `codex`), plus a failure row for any
harness file that failed to load at all.
