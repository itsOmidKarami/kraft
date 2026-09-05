# Sub-project F: the findings contract

Date: 2026-09-04
Beads: Kraft-8mu.6 (parent Kraft-8mu); child Kraft-8mu.6.1 blocked by Kraft-8mu.2

## Problem

`02_orchestrator_core.md` §7.2 specifies a fix loop driven by structured
failures: a table of non-clean results per measuring task, a fix task whose
prompt carries "the structured failure payload", and `Finding{severity, message,
file, line, source_plugin}` as the shared schema across review plugins
(`03_plugin_adapters.md` §4).

None of that is in the code. The loop itself is built and works
(`executor.py:221-323`), but it branches on task *status* only:

- `_measure_node` returns a list of failed hook-point **names**.
- `_FIX_PROMPT` interpolates exactly those names: *"Failing hook points:
  on.test.run"*. The fix agent is told which check failed, never what it said.
- `# fix task status is not branched on` — the loop re-measures blindly.
- `grep -rn "Finding" src/` returns nothing.

Three consequences, each of which a superpowers skill has already solved in
practice:

**Every finding is fatal.** `02` §13 defers a severity threshold, so any
non-clean result of any severity burns a fix cycle. A style nit and a data-loss
bug cost the same.

**The loop cannot see itself repeating.** `02` §14 defers "no-progress early
escalation" explicitly because defining no-progress "needs a track record":

> Deferred: defining "no progress" (same failures? same files touched? diff
> churn without green?) needs a track record.

**The fix agent starts blind every cycle**, holding a hook-point name.

`subagent-driven-development` runs the same loop against real work and answers
all three. Its re-review template verdicts **each finding** ADDRESSED or NOT
ADDRESSED; its skill body routes Minor findings out of the loop into a roll-up
the final review triages; its task-reviewer template carries a rubric hardened
against reviewer failure modes. The track record §14 was waiting for exists.

This sub-project ports the answers.

## Non-goals

- **Building a review plugin.** `on.review.local.run` and `on.review.mr.run` are
  `builtins.noop`. This spec defines the contract they will satisfy; it does not
  satisfy it. `Kraft-pl7` still owns identifying the remote review tool.
- **A findings table.** See §4 — the event log already is one.
- **Changing the cap machinery.** `retry_counters`, `resolve_cap` and
  `policy.check` are correct and untouched.
- **Findings from `on.test.run`.** A pytest subprocess emits an exit code. §6
  covers what happens to tasks that produce no findings: nothing changes for
  them.
- **Rulings and ledgers.** SDD's controller adjudicates alone and confesses
  afterward because it has no way to stop. Kraft has gates. See §7.

## 1. The result-file contract

`adapters/subprocess.py` already reads the result file at `$KRAFT_RESULT_PATH`
twice — `_resolve_result_file` for `status`, `read_summary_ref` for
`session_summary_ref`. Findings are a third reader of the same file, not a new
channel:

```json
{
  "status": "done",
  "findings": [
    {
      "severity": "important",
      "message": "swallowed exception hides a write failure",
      "file": "src/kraft/store.py",
      "line": 214,
      "source_plugin": "ponytail-review"
    }
  ]
}
```

`severity` is one of `critical`, `important`, `minor`. `message` and
`source_plugin` are required; `file` and `line` are optional, because a finding
about a missing file has no line.

Malformed entries are dropped individually and counted, matching the file's
existing best-effort posture — `read_summary_ref` swallows a broken file rather
than failing the session. A result file whose `findings` key is absent or not a
list yields no findings, which is the compatibility path in §6.

**A non-clean status with an empty `findings` list is still non-clean.** The
findings list refines *why* a task failed; it is not the definition of failure.
Conflating the two would make a reviewer that crashed before writing findings
look clean.

## 2. Severity gate

`policy.yaml` gains:

```yaml
findings:
  loop_severities: [critical, important]
```

Only findings at those severities enter the fix loop. Minor findings are
recorded and carried to the `human_review` gate as a roll-up.

SDD's rule about that roll-up is the part worth copying verbatim, because it is
the failure mode and not the feature:

> A roll-up nobody reads is a silent discard.

So the deferred-minor list is not merely stored — it is rendered at the
`human_review` gate, beside the diff from `Kraft-8mu.2`, as the list the human
triages before approving. A minor finding that never reaches a human was
discarded, and the config would be lying about deferring it.

Setting `loop_severities: [critical, important, minor]` restores today's
behaviour exactly, which is what makes this safe to ship.

## 3. Finding identity

Carry-forward needs a finding to be recognizable across cycles. The fingerprint:

```
sha256(source_plugin + "\0" + file + "\0" + normalized(message))[:16]
```

**`line` is deliberately excluded.** The fix task edits the file, so every line
number below the edit shifts; including `line` would make every finding look new
after any fix, which is precisely the blindness this sub-project is removing.
`normalized(message)` is whitespace-collapsed and lowercased — enough to survive
a reviewer rephrasing its own output between runs, not enough to merge two
genuinely different findings in the same file.

This is a heuristic and will occasionally merge two findings or split one. That
is acceptable: the consequence of a wrong fingerprint is one extra fix cycle or
one early escalation to a human, both of which are recoverable and visible.
A precise identity would require reviewers to emit stable ids, which is a
contract no external review tool will honour.

## 4. Carry-forward, and no-progress escalation

Each measuring pass emits one event:

```
findings_measured  { node_id, cycle, findings: [...], fingerprints: [...] }
```

`findings` is every finding the cycle produced, at every severity — it is the
record, and §2's deferred-minor roll-up reads it. `fingerprints` is the
**loop-eligible subset only**, because its single purpose is the cycle-to-cycle
comparison below, and a Minor finding that never enters the loop must not be able
to hold the loop open by appearing unchanged.

**No new table.** The `events` table is append-only, ordered by `seq`, indexed
by work item, and already durable across restarts — SDD needs a ledger file
precisely because it has no such store, and copying `progress.md` into a system
that has an event bus would be a regression. The last two `findings_measured`
events for a node are one query.

**The event log is the state, not a copy of it.** The previous cycle's
fingerprints are read back from the last `findings_measured` event for the node,
never carried in a local variable across loop iterations. `_reconcile_current_node`
re-enters `_walk_node` after a crash or a resume with the retry counter intact, so
a loop holding its history in the stack frame forgets everything it has seen and
can never escalate — on exactly the path that motivates the feature.

Per cycle, after measuring:

| Comparison against the previous cycle | Action |
|---|---|
| loop-eligible set is empty | node completes |
| set shrank, or any fingerprint is new | ordinary fix cycle |
| set is unchanged and non-empty, **and a fix cycle ran since it was last measured** | **no progress** — escalate |

That last precondition is not decoration. Without it the comparison means "the same
as the last measurement" rather than "survived a fix", and the two diverge on
re-entry: `POST /work-items/{id}/retry` re-enters `_walk_node` at round 0, which
*measures first*. A deterministic reviewer on unchanged code then reproduces the
escalating cycle's set and the item stops again without ever dispatching a fix —
discarding the steer the human typed on the way in. The recovery this section
promises would be a no-op.

**A clean status wins over the findings set.** A measuring task that exits `done`
while emitting findings completes the node; the findings are recorded in the event
but do not enter the loop. The table above keys on the eligible set because that is
the interesting case, but status is the reviewer's own verdict on its own work, and
a reviewer that says "done" has said the code is acceptable.

That middle row is the answer §14 was waiting for. "No progress" is: *the same
loop-eligible fingerprints survived a full fix cycle.* Not diff churn, not files
touched — the reviewer said the same thing twice about code that changed in
between.

Escalation goes to `needs_human` with its own reason
(`no_progress: <n> finding(s) unchanged across cycle <c>`), reusing the same
path as a cap breach — `mark_sessions_capped_out` is not used, because the
sessions did not cap out. The `<node>_fix_loop` counter is left as it stands — the escalation does not
clear it. What a human's retry does with it is `retry_after_cap`'s business, and
that deletes the counter row outright, so the retry starts from a fresh budget
rather than resuming a spent one.

The design's stated destination for this was the guidance gate
(`on.guidance.provided`), which is not built. `needs_human` with a distinct
reason is the same stop with a name the UI already renders, and it upgrades to
the guidance gate later without changing this rule.

## 5. What the fix task is given

`_FIX_PROMPT` gains the findings, replacing the bare hook-point list:

- the loop-eligible findings verbatim, with `file:line` and severity
- which of them are **repeats** from the previous cycle, marked as such
- the standing instruction to make no unrelated changes

The repeat marking matters more than the findings themselves. An agent told "you
already tried to fix this one and the reviewer disagreed" behaves differently
from one seeing it fresh — this is SDD's rounds-4-5 framing ("a prior implementer
attempted this task N times; you own it now") reduced to what a one-shot CLI can
carry.

Carrying the previous fix session's result file into the prompt belongs to
`Kraft-8mu.7`, not here.

## 6. Compatibility

A task whose result file has no `findings` key behaves exactly as it does today:
status-driven, every failure enters the loop, no severity gate, no carry-forward.
`on.test.run` is `pytest -q` through the subprocess adapter and will never emit
findings, so the `verify` node keeps working unchanged while
`on.review.local.run` is still `noop`.

This is what lets the contract land before any producer of it exists — and it is
the same reason to land it now rather than after: the review plugin then has a
schema to write against instead of inventing one.

## 7. What is deliberately not ported from SDD

- **The ledger.** `events` is a better version of it. See §4.
- **Rulings.** SDD's controller decides alone and reports the decisions
  afterward because a running plan cannot wait for a human. Kraft stops at gates;
  that is the product. A "Ruling" concept would be an autonomy Kraft has
  deliberately declined.
- **The five-round cap.** Kraft's caps are per-loop, in `policy.yaml`, snapshotted
  per counter row. Strictly better than a constant in prose.
- **Adjudicate-only-at-the-cap.** That rule exists to stop a controller
  short-circuiting its own loop. Kraft's loop is executed by a policy engine that
  cannot rationalize.

## 8. The reviewer contract

When the review plugin is built, its injected instruction carries these rules.
They are recorded here because they are the expensive half of SDD's task-reviewer
template — each one is a reviewer failure mode observed in real sessions:

- Emit `Finding{}` objects to the result file, never prose.
- **Do not trust the implementer's report.** A stated rationale — "left it per
  YAGNI", "kept it simple deliberately" — never downgrades a finding's severity.
  That is the author grading their own work.
- **Do not re-run tests the measuring task already ran.** Run a focused test only
  when reading the code raises a specific doubt.
- **Plan-mandated is still a finding**, labeled as such. Whoever wrote the plan
  does not get to grade it.
- Warnings and noise in test output are findings; output should be pristine.
- The review is **read-only** on the checkout — no mutation of the working tree,
  index, HEAD, or branch state.
- A requirement that cannot be verified from the diff is reported as such, not
  answered by crawling the repository.

And one rule for whoever composes that instruction, which SDD states as a
prohibition on the *controller*:

> If the prompt you are writing contains "do not flag", "don't treat X as a
> defect", "at most Minor", or "the plan chose" — stop: you are pre-judging.

A registry hook instruction that tells a reviewer what not to find produces a
clean result that means nothing.

## 9. The review package (`Kraft-8mu.6.1`, blocked by `Kraft-8mu.2`)

A review task today runs in the worktree with no diff handed to it. It can read
files, so it reviews *the code*; it cannot see *the change*, which is what a
review is about. `subagent-driven-development` never dispatches a reviewer
without one — "Never dispatch a task reviewer without a diff file" — and its
`review-package` script writes three things into one file:

```
## Commits      git log --oneline BASE..HEAD
## Files changed  git diff --stat BASE..HEAD
## Diff         git diff -U10 BASE..HEAD
```

`-U10` rather than the default 3 is deliberate there, and the reviewer template
says why: *"The diff's context lines ARE the changed files: do not Read a
changed file separately unless a hunk you must judge is cut off mid-function."*
Ten lines of context turns one Read into the whole review surface.

Kraft's version is the same three commands against `work_items.base_ref`, which
`Kraft-8mu.2` introduces — hence the dependency. The package is written into
`run_dirs.results` beside the session's own result file, named by the range so a
re-measure after a fix cycle gets a distinct file rather than overwriting the
evidence of the previous one.

**Handed over by path, not by content.** The path goes into the review task's
invocation the way `KRAFT_RESULT_PATH` already does; the diff never passes
through a prompt. A large diff pasted into every review of every cycle of every
work item is the same cost `Kraft-8mu.7` §4 refuses for result files.

**One producer, two consumers.** The same function serves the review hooks and
`Kraft-8mu.2`'s `GET /work-items/{wid}/diff`. The endpoint already computes
`git diff <base_ref>` plus a numstat; the package adds the commit list and wider
context. Building them separately means two things that disagree about what the
change is — and the human at the gate and the reviewer that gated it must be
looking at the same diff, or a clean review means nothing.

**Ordering:** land `Kraft-8mu.2` first, then factor its diff production into a
shared helper as this task's first step rather than writing a second one.

## Acceptance

- A result file with `findings[]` drives the loop; one without behaves as today.
- (`Kraft-8mu.6.1`) A review task is handed a package path; the human at the
  gate and the reviewer see the same diff, from one producer.
- A `minor`-only result completes the node and the finding appears at the
  `human_review` gate.
- Two consecutive cycles with an unchanged loop-eligible set stop the item with a
  `no_progress` reason before the cap is exhausted.
- The fix prompt names the findings and marks the repeats.
- `just test`, `just test-ui`, `just lint` pass.
