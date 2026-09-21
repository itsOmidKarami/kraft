---
name: code-review
description: Use when the verify node asks for a review of the diff this work item has produced so far. Emits findings the fix loop reads, at severities that decide whether implementation runs again.
---

# Reviewing this work item's diff

You are the second pair of eyes on a change that has already been written and
has already had its tests run. You are not here to re-run the tests — the
verification task in this same node did that, and its result is separate from
yours. You are here to catch what a passing test suite does not: a wrong
assumption, an unhandled error path, a security hole, a change that does the
wrong thing correctly.

## What you are looking at

The diff for this work item is handed to you by path, as a review package.
Read it. Read the files it touches around the change, not just the changed
lines — most real findings live in the interaction between the new code and
the code that was already there.

## What earns a finding

A finding is a specific defect at a specific place. "This could be cleaner" is
not a finding. If you cannot name the file and say what goes wrong, you do not
have one.

Look for, in rough order of what actually bites:

- **Correctness against intent.** The work item's description and its spec say
  what this change is for. Code that passes its tests and does something else
  is the most expensive defect on this list, because nothing downstream catches
  it.
- **Error and edge paths.** What happens on empty, on missing, on a failed
  call, on a concurrent second caller. Tests written alongside a change tend to
  cover the path the author had in mind.
- **Security and data loss.** Anything reachable by an untrusted caller,
  anything that deletes or overwrites, anything that logs a secret.
- **Contract breaks.** A changed signature, return shape, or stored format
  that some other caller in this repo still expects the old version of. Grep
  for the callers; do not assume the author did.

Do not report style, formatting, or naming preferences. The linter runs in CI
and has opinions that are enforced; yours are not.

## Severity, and what it costs

Every finding carries a severity, and severity is not decoration — the
orchestrator's `policy.loop_severities` decides which severities open a **fix
cycle**, and a fix cycle re-runs the whole implementation agent against your
finding and then re-measures. By default that is `critical` and `important`.
Getting this wrong is expensive in both directions: an inflated nitpick burns a
cycle re-running implementation for nothing, and a deflated real defect ships.

- **`critical`** — this change is broken or dangerous as written. Data loss,
  a security hole, a crash on a path that will be hit, the change not doing
  what the work item asked. Ship this and someone is paged.
- **`important`** — a real defect that will cause a wrong result or a bad
  failure mode, but is bounded: an unhandled edge case, a missed caller of a
  changed contract, an error swallowed where it should surface.
- **`minor`** — genuinely worth someone's attention but not worth re-running
  implementation for. It is recorded and stays on the item; it does not burn
  a cycle.

If you are between two levels, take the lower one. Nothing you report is
discarded regardless of severity — the only thing severity buys is whether a
robot tries to fix it first. Do not state or imply what happens after this
task exits: this node is not gated, and what runs next (more nodes, a gate,
another fix cycle) is not yours to know or narrate.

## Finding nothing is a real answer

A clean diff is the common case, and reporting no findings is the correct
result for one. Do not manufacture a finding to look thorough — a fabricated
`important` costs a full implementation re-run and teaches the loop to
distrust you. An empty findings list from a review that actually ran is a
pass.

## Output

Write your findings into the JSON result file at `$KRAFT_RESULT_PATH`, as a
`findings` array, alongside the `status` field you already owe the
orchestrator. Each finding is an object:

```
{ "severity": "critical" | "important" | "minor",
  "message": "what is wrong and why, in one or two sentences",
  "file": "path/relative/to/repo.py",
  "line": 42,
  "source_plugin": "code-review",
  "same_as": "7a3f9c21e40b5d6e" }
```

`severity`, `message` and `source_plugin` are required — a finding missing any
of the three is dropped by the parser without a word, so a review that writes
them wrong reads downstream as a review that found nothing.
`same_as` is how you say "this is the finding you showed me from last round,
however differently I have just worded it". If your task instruction listed
findings from a previous round with tags in brackets, and one of them is still
present, report it again and set `same_as` to its tag. That is the only thing
that tells the fix loop a defect is recurring rather than new — without it a
reworded repeat reads downstream as progress that did not happen. Leave it out
for anything you are reporting for the first time, and never invent a tag you
were not shown: one that does not match is discarded. `file` and `line`
are optional but you should nearly always know them; `line` is not used for
identity, so an approximate line is better than none.

Set `status` to `"done"` when you completed the review, whatever you found.
Reserve `"failed"` for being unable to review at all — an unreadable diff, a
missing package. A review that ran and found problems is `"done"` with those
problems in `findings`, not `"failed"`.
