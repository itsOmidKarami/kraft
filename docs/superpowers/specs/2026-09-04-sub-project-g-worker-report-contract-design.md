# Sub-project G: the worker report contract

Date: 2026-09-04
Beads: Kraft-8mu.7 (parent Kraft-8mu); child Kraft-8mu.7.1 blocked by Kraft-8mu.3

## Problem

An agent session can end in one of two ways Kraft understands.
`adapters/subprocess.py:33`:

```python
return status if status in ("done", "failed") else "failed"
```

Anything else in the result file's `status` becomes `failed`. The
`worker_sessions.status` CHECK constraint allows `pending`, `running`, `done`,
`failed`, `capped_out`, `paused`, `unknown`, but only the first two are things
the agent itself can choose.

So an agent that finished the work and doubts it has to claim `done`. An agent
that could not proceed because it was never told something has to claim
`failed` — and `failed` in a `fix_loop` node burns a cycle, while `failed`
outside one lands `needs_human` with the reason *"task failed"*, which tells the
human nothing about what was missing.

`subagent-driven-development`'s implementer template makes this the worker's
first-class output, with four statuses and an instruction that names the
failure it prevents:

> Use DONE_WITH_CONCERNS if you completed the work but have doubts about
> correctness. Use BLOCKED if you cannot complete the task. Use NEEDS_CONTEXT
> if you need information that wasn't provided. **Never silently produce work
> you're unsure about.**

Kraft has one of the four already: `plan_diverged` routes to the guidance gate,
which is `BLOCKED`. The other two have nowhere to go.

Second, smaller problem: each fix cycle launches a fresh
`on.implementation.start` that knows nothing of the previous attempt.
`executor.py:316` passes a prompt built from the node id and the failed hook
names; the prior fix session's result file sits in `run_dirs.results` unread.
SDD resumes the same implementer for rounds 1-3 specifically because "its
context is intact: it knows the task, the code, and its own choices." Kraft
cannot resume a one-shot CLI, but it can hand over the artifact.

## Non-goals

- **Resuming a live agent process.** `run_agent_task` is one-shot by design and
  `reattach.py` recovers a crashed session, not a finished one. The report file
  is the persistent memory — the same fallback SDD names for harnesses that
  cannot message a live subagent.
- **A mid-run question channel.** `needs_context` is a terminal status, not an
  interactive prompt. Kraft's answer to "ask the human" is stopping, and this
  reuses that.
- **Implementing** model escalation — §6 designs it; `Kraft-8mu.7.1` builds it
  once sub-project C supplies the model plumbing to escalate *with*.
- Changing what `capped_out`, `paused` or `unknown` mean.

## 1. Two new terminal statuses

Schema migration at the next free version adds two values to the
`worker_sessions.status` CHECK constraint. SQLite cannot alter a CHECK in place,
so this follows the table-rebuild pattern already in `_MIGRATIONS[4]`.

```
'pending','running','done','failed','capped_out','paused','unknown',
'done_with_concerns','needs_context'
```

`_resolve_result_file` widens its accepted set to match. Anything still
unrecognized stays `failed` — the existing conservative default is correct.

**The agent has to be told these exist.** `adapters/agent.py`'s injected context
describes the session-summary contract and nothing else — the word `status`
appears in it only as `session_summary_ref`. A status vocabulary no agent knows
about is inert, so the same context that already explains how to write a session
summary gains a short paragraph naming the four statuses and the two optional
fields, and §5's one-question-per-stop instruction goes there too.

Result-file fields, both optional and both free text:

```json
{ "status": "done_with_concerns", "concerns": "the retry path is untested" }
{ "status": "needs_context",      "question": "which branch should the MR target?" }
```

## 2. `done_with_concerns`

**Progression:** identical to `done`. The chain advances, the node completes, a
fix loop treats it as a clean fix attempt. An agent that finished the work has
finished the work.

**Visibility:** the concerns text is recorded on the session and surfaced at the
next gate the item reaches, alongside the deferred-minor roll-up from
sub-project F. Both are the same shape of thing — something a machine noticed,
deferred, and owes a human before approval — and they belong in one place at the
gate rather than in two competing panels.

An item that reaches `merge` without passing a gate carries its concerns into
the completion event, so they are recoverable from the timeline rather than
lost.

The point of the status is not to change control flow. It is that an agent with
doubts currently has to either overstate confidence or fail the task, and both
destroy information the human wanted.

## 3. `needs_context`

**Progression:** stop. `work_items.status = needs_human`, reason
`needs_context: <question>`.

The *transaction* already exists — a human types something, the next launch
carries it — but the claim that it "reuses the resume path unchanged" was wrong,
and a plan review caught it before any code was written:

- `POST /work-items/{wid}/steer` and `POST /work-items/{wid}/resume` both
  `409` unless `work_items.status == "paused"` (`api.py:790`, `api.py:803`).
- A `needs_context` stop sets `needs_human`, not `paused`.
- `POST /retry` refuses any node without a `fix_loop` (`api.py:864`).

So as originally specified, a `needs_context` stop on a plain node was a **dead
end**: no API and no UI could un-stick it. The status would have been a way to
strand a work item.

**The fix is to widen the two guards, not to add an endpoint.** Both currently
mean "you may only steer something that is stopped and waiting for you", and
they express it as `paused` because that was the only such state when they were
written. A `needs_context` stop is precisely that state under a different name.
They become: `paused`, or `needs_human` whose reason is a `needs_context`.

Nothing else about the transaction changes — `set_steer`, `take_steer` and the
prepend into the next launch are all reused as-is.

The UI half is real work, not a relabel: `PausedCard` renders only for
`status === "paused"`, and a `needs_human` item without a fix loop falls through
to a control row whose Steer button is hard-disabled. The answer box has to be
rendered for this state, not renamed.

`_STEER_PROMPT`'s wording is worth a second look here — "A human has steered this
run" reads oddly as the answer to a question the agent asked. A variant that
leads with the question keeps the prompt honest about which of the two situations
produced it.

**Not a fix cycle.** A `needs_context` measuring task stops the item rather than
counting a cycle: the loop's cap exists to bound *attempts at the work*, and an
agent that never had the information did not attempt it.

Two corrections to how this was first written. `02` §7.2 makes the same carve-out
for `plan_diverged`, but that is documented, not implemented — `grep -rn
plan_diverged src/kraft/` finds nothing — so it is a precedent in prose only and
there is no code to copy. And the counter is bumped *before* the fix task is
dispatched, so a `needs_context` returned by the fix task cannot avoid consuming
the cycle it is already inside; the carve-out is only meaningful for a
`needs_context` from a **measuring** task, which is where the check must sit.

## 4. Carrying the previous attempt forward

When a `fix_loop` node launches fix cycle N > 1, the prompt carries the path to
cycle N-1's result file, and — when the fix task was an agent — its session
summary ref.

The file is handed over by **path, not by content**. SDD states the reason
plainly, having paid for it:

> Everything you paste into a dispatch prompt stays resident in your context for
> the rest of the session and is re-read on every later turn. Hand artifacts over
> as files.

Kraft's version of that cost is tokens on every fix cycle of every work item.
`run_dirs.results / f"{session_id}.json"` is already a path the agent can read,
and the agent already writes to `$KRAFT_RESULT_PATH`, so both directions of the
channel exist.

Combined with sub-project F's repeat marking, the fix agent on cycle 3 gets: the
findings, which of them it already failed to fix, and its own previous notes.
That is as close to SDD's "resume the implementer" as a one-shot CLI allows.

## 5. What this does not solve

An agent still cannot ask a question and continue. `needs_context` costs a full
stop and a relaunch, so an agent that needs three facts will either guess at two
or stop three times.

The mitigation is instruction, not mechanism: the launch context should say to
report every missing fact in one `needs_context`, the way SDD's implementer
template front-loads "**Ask them now.** Raise any concerns before starting work."
A real interactive channel would mean a resident agent process, which is a
different execution model and not one this spec proposes.

## 6. Model escalation on later cycles (`Kraft-8mu.7.1`, blocked by `Kraft-8mu.3`)

`subagent-driven-development` splits its fix loop at round 4:

> **Rounds 1-3 — resume the original implementer.** Its context is intact.
> **Rounds 4-5 — dispatch a fresh implementer on a more capable model**, with
> this framing: "A prior implementer attempted this task [N] times; you own it
> now." A loop that survives three resumes usually means the implementer cannot
> see its own problem — fresh eyes and a capability bump in one move.

Kraft gets the fresh eyes for free — every fix cycle is already a new one-shot
process. What it cannot currently do is the capability bump, because no cycle
can choose a model. Sub-project C makes that possible; this section says when to
use it.

**When.** A per-loop threshold in `policy.yaml`, beside the caps that already
live there, because "how many cycles before escalating" is the same kind of
decision as "how many cycles before stopping":

```yaml
loops:
  verify_fix_loop: { attempts: 5, wall_clock_s: 3600, escalate_after: 2 }
```

**Which model.** Not in `policy.yaml`. Sub-project C establishes that model
selection lives in `registry.yaml` per hook and `repos.yaml` per repo, and a second
place to name a model would fight that precedence chain. The hook entry gains a
sibling to `model:`:

```yaml
on.implementation.start: { kind: agent, command: claude, model: sonnet, escalate_model: opus }
```

Cycles at or below `escalate_after` use `model`; cycles above it use
`escalate_model` when set, falling back to `model` when not. An unset
`escalate_after` means no escalation, which is today's behaviour and the
default.

**Interaction with no-progress escalation.** `Kraft-8mu.6` §4 stops the item
when a cycle changes nothing. That check runs first: if the same findings
survived a cycle, a more expensive model on the same blind approach is money
spent on a stop that is already warranted. Escalation is for a loop that *is*
making progress and has not converged — not for one that is stuck, which is what
the human is for.

That ordering is the whole reason these two live in different sub-projects and
still have to be read together.

## Acceptance

- An agent returning `done_with_concerns` advances the chain, and its concerns
  reach the next gate.
- An agent returning `needs_context` stops the item, shows its question, and is
  answered through the existing steer box.
- A `needs_context` inside a fix loop does not consume a cycle.
- Fix cycle N > 1 names cycle N-1's result file by path.
- An unrecognized status still resolves to `failed`.
- (`Kraft-8mu.7.1`) A fix cycle past `escalate_after` launches with
  `escalate_model`, and no-progress escalation is evaluated before it.
- `just test`, `just test-ui`, `just lint` pass.
