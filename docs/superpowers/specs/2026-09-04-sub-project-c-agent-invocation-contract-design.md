# Sub-project C: the agent invocation contract

Date: 2026-09-04
Beads: Kraft-8mu.3 (parent Kraft-8mu); blocks Kraft-8mu.5, Kraft-n70

## Problem

Three separate gaps — no model choice, no agent sandbox, no place for per-repo
standards — are one spec, because all three change the same twenty lines.
`adapters/agent.py:73` builds every agent launch:

```python
cmd = [
    *shlex.split(command),
    "-p", task_instruction,
    "--append-system-prompt", ctx,
    "--output-format", "json",
]
```

Model choice adds a flag. Permissions add flags. Steering adds text to `ctx`.
Specified apart, this function is rewritten three times and the three features
arrive with no agreed precedence between them.

There is a fourth problem hiding in that snippet, and it is the one that decides
the shape of the whole sub-project: every flag there is Claude-CLI-specific.
`-p`, `--append-system-prompt`, `--output-format json` are not a generic agent
interface. Adding `--model` and `--allowed-tools` directly to `registry.yaml`
would hardwire one vendor's CLI into user-facing config — the same mistake
sub-project D unwound for GitLab, made a second time. `Kraft-n70`
(Codex support) is blocked on exactly this.

## Non-goals

- **Containerizing the agent.** `2026-09-04-packaging-and-dev-execution-design.md`
  rejected Docker because Kraft shells out to CLIs holding host credentials, and
  that reasoning is unchanged.
- **A mid-run permission prompt.** Gates are Kraft's answer to "ask the human".
  An agent blocking on a modal is a second, unbounded stop with no policy behind
  it.
- **Overturning the context-injection boundary.** See §4.
- **Per-node model selection in chain templates.** Templates describe process,
  not execution config; see §2.
- **A real OS sandbox.** Named ceiling, §3.

## 1. Agent profiles

A profile is a table mapping Kraft's neutral options onto one CLI's flags. It
lives in code, not user config — it is a fact about a CLI, and a user editing it
is a user reporting a bug.

```python
# adapters/agent.py
Profile = namedtuple("Profile", "prompt system_prompt output_json model deny_tools")

PROFILES = {
    "claude": Profile(
        prompt=("-p",),
        system_prompt=("--append-system-prompt",),
        output_json=("--output-format", "json"),
        model=("--model",),
        deny_tools=("--disallowed-tools",),
    ),
}
```

`registry.yaml` gains an optional `profile:` on agent-kind hooks, defaulting to
`claude`:

```yaml
on.implementation.start: { kind: agent, command: claude, model: opus, deny_tools: [WebFetch] }
```

`command` stays the executable, so `command: claude-next` with the same profile
keeps working. An unknown profile is a load-time validation error from
`load_registry`, alongside the existing hook validation — a typo must fail at
save, not at the first launch three nodes into a chain.

This is what makes `Kraft-n70` a table entry. It is also the reason `model:` and
`deny_tools:` are spelled neutrally rather than as flags.

The envelope parsing in `_envelope_is_error` stays Claude-shaped and is left
alone. It is already isolated behind `post_resolve`, and generalizing a parser
against one known format is guesswork; it becomes a profile field when a second
CLI's envelope is in hand.

## 2. Model

`registry.yaml` hook entries take `model: <string>`; `repos.yaml` entries take a
`default_model`. Precedence, most specific first:

| Source | Scope |
|---|---|
| `registry.yaml` hook entry | this hook point, everywhere |
| `repos.yaml` entry | every hook in this repo |
| unset | the CLI's own default |

Chain templates deliberately cannot set a model. A template is the process — the
nodes and gates a work item moves through — and it is shared across repos. Model
choice is an execution and cost decision that varies per machine and per
account. Putting it in the template makes templates unshareable, and the
template is the artifact Kraft is trying to make portable.

Kraft does not validate the model string. It has no model list, the list changes
without Kraft changing, and an unknown model is an error the CLI reports well.
The launch failure surfaces through the existing `failed` session path with the
CLI's own message in the log.

`worker_sessions.model` is already a column and `analytics.py` already groups on
it, so cost-per-model reporting arrives with no further work. That is the point
of the feature: Kraft already measures spend it cannot currently steer, and
`on.ci.poll` running the same model as `on.implementation.start` is the largest
single waste it can see.

## 3. Permissions

Two tiers, and the spec must be plain about which one ships.

**Tier 1 — declared tool denial, delegated to the CLI.** `registry.yaml` hook
entries take `deny_tools: [...]`, mapped through the profile to the CLI's own
disallow flag. A review hook does not need to write files; a poll hook does not
need a shell. Denying per hook point is meaningful because Kraft, unlike a chat
session, knows what each invocation is for.

**The ceiling, stated rather than implied:** this is the agent CLI policing
itself, at Kraft's request. It stops an agent from using a tool it was told not
to use. It does not stop a compromised or jailbroken agent, and it does not
contain a shell that the agent is still permitted to run. Kraft's real
containment today is that `cwd` is a per-item git worktree — an isolation of
blast radius, not a security boundary.

**Tier 2 — OS-level confinement.** `sandbox-exec` on macOS, bubblewrap or
seccomp on Linux, wrapping the launch so the process cannot write outside its
worktree or reach the network unless the hook asked for it. This is the tier
that makes "unattended runs on someone else's repo" a defensible claim. It is
deferred: it is per-platform, it is the kind of thing that breaks agent CLIs in
ways that are hard to diagnose, and shipping tier 1 first gives the declaration
syntax that tier 2 will enforce. The `deny_tools` field is chosen so tier 2 can
enforce the same declarations without a config change.

Do not let a release note claim sandboxing on the strength of tier 1.

## 4. Steering, and the boundary it must not break

The glossary in `00_overview.md` records a decided rule:

> **Context-injection boundary** — process/steering context reaches agents only
> via per-invocation system-prompt / MCP config, never via `CLAUDE.md`,
> `AGENTS.md`, or any repo file.

Kiro's steering files are repo files, so the naive port of the feature would
break this rule outright. It should not, and does not need to. The boundary
governs the *channel*, not the existence of standards. `agent.py:76` already
injects `_CTX` through `--append-system-prompt`, which is the sanctioned
channel; the gap is that there is no authored content to put through it.

So: steering is Kraft-owned files, injected through the existing system prompt.

```
$KRAFT_HOME/templates/steering/<name>.md
```

Edited by a Settings screen like every other file under `templates/`, diffable
and revertable for the same reasons. Referenced by name:

- `repos.yaml` entry: `steering: [house-style, python]` — every hook in the repo
- `registry.yaml` hook entry: `steering: [review-rubric]` — that hook everywhere

Both apply, repo first then hook, appended to `_CTX` under a heading. Order is
fixed and documented rather than merged cleverly: a reader debugging a prompt
needs to predict what the agent saw.

A missing steering name is a load-time validation error, same as an unknown
profile.

**The boundary is preserved, and the spec must say why in the file itself:**
Kraft reads `templates/steering/*.md` from `$KRAFT_HOME` and never reads or
writes `CLAUDE.md`, `AGENTS.md`, or anything else inside the target repo. A
future reader finding a steering feature next to a rule banning steering files
will assume the rule was forgotten unless the distinction is written down.

Total budget for injected steering is capped (8 KB) with a load-time error above
it. An oversized system prompt degrades every launch quietly and costs money on
each one.

## 5. Precedence, in one place

When all three features are configured at once, one resolution function produces
the launch — not three call sites:

```
resolve_invocation(hook_entry, repo_entry) -> Invocation(
    command, profile, model, deny_tools, steering_texts
)
```

`run_agent_task` takes an `Invocation` and does no lookup of its own. This is the
structural reason the three features are one spec: the precedence rules are
trivial individually and only interact here.

## 6. Testing

- profile: unknown name fails `load_registry`; known name maps to the right
  flags; `command` and `profile` vary independently
- model: hook beats repo beats unset; the flag is absent entirely when unset,
  rather than passed empty
- deny_tools: hook and repo lists both reach the command line
- steering: repo then hook ordering; missing name fails at load; over-budget
  fails at load; the resulting `--append-system-prompt` contains `_CTX` and both
  steering bodies
- boundary regression: a launch touches no file inside the target repo other
  than what the agent itself writes — asserted against the fake agent
- `resolve_invocation` unit tests for the full precedence matrix

The existing `fixtures/fake-claude.sh` accepts and ignores unknown flags, so the
suite exercises real command lines without a real agent.

## Acceptance

- `on.ci.poll` and `on.implementation.start` run different models, and
  `analytics.py` reports cost split by model with no analytics change.
- A hook can be denied a tool and the denial appears on the command line.
- Per-repo standards reach the agent with no file written into the repo.
- Adding a second agent CLI is a `PROFILES` entry plus an envelope parser, and
  touches no user-facing config schema.
- `just test`, `just test-ui`, `just lint` pass.
