---
name: code-review
description: "Reviews the diff a work item has produced for defects its passing tests miss, and writes severity-ranked findings to the result file. Kraft runs it as the review task of a verification node, after the test step is green; use it when that task asks for a review of the change so far."
---

# Reviewing this work item's diff

You are the second pair of eyes on a change that has already been written and
whose tests have already passed. Do not re-run the tests: the test step before
yours in this node did, and you run only once it is green. Change, write and
commit nothing in the worktree either, whatever the instructions above say
about committing: a repair agent acts on your findings, and Kraft stops the
item if the worktree moved under a review. Confirm a suspected defect by
reading the code; a scratch file counts as a change. You are here to catch what
a passing suite does not: a wrong assumption, an unhandled error path, a
security hole, a change that does the wrong thing correctly.

## What you are looking at

The diff for this work item is handed to you by path, as a review package.
Read it first; its context lines already show the changed files. Most real
findings live in the interaction between the new code and the code that was
already there, so read that code too — callers, neighbours, whatever the
package does not show — rather than judging the changed lines alone.

## What earns a finding

A finding is a specific defect at a specific place. "This could be cleaner" is
not a finding. If you cannot name the file and say what goes wrong, you do not
have one.

Look for, in rough order of what actually bites:

- **Correctness against intent.** The work item's description and its spec say
  what this change is for: the task line and documents list in your
  instructions point at them, and with no spec, judge against the task line.
  Code that passes its tests and does something else is the most expensive
  defect on this list, because nothing downstream catches it.
- **Error and edge paths.** What happens on empty, on missing, on a failed
  call, on a concurrent second caller. Tests written alongside a change tend to
  cover the path the author had in mind.
- **Security and data loss.** Anything reachable by an untrusted caller,
  anything that deletes or overwrites, anything that logs a secret.
- **Contract breaks.** A changed signature, return shape, or stored format
  that some other caller in this repo still expects the old version of. Grep
  for the callers; do not assume the author did.
- **Test shape.** If the repo's own instructions set a test convention (where a
  new case goes, which helpers to reuse), a diff that breaks it is `minor`: it
  costs someone's attention, not a wrong result. With no stated convention,
  there's no finding here.

If your instructions name an intent tree, two more cases earn an `important`
finding: the diff changes behaviour a requirement in the tree describes without
changing that requirement; or a requirement this diff adds or changes is stated
more broadly than the test it is pinned to enforces. A new requirement with no
pin yet is not a finding — it is legal, and the check reports it.

Do not report style, formatting, or naming preferences. The linter runs in CI
and has opinions that are enforced; yours are not.

## Severity, and what it costs

Every finding carries a severity, and severity is not decoration — the
orchestrator's `policy.loop_severities` decides which severities open a **fix
cycle**, and a fix cycle dispatches a repair agent against your finding and then
re-runs the tests and this review. By default that is `critical` and
`important`. Getting this wrong is expensive in both directions: an inflated
nitpick burns a repair and a second review for nothing, and a deflated real
defect ships.

- **`critical`** — this change is broken or dangerous as written. Data loss,
  a security hole, a crash on a path that will be hit, the change not doing
  what the work item asked. Ship this and someone is paged.
- **`important`** — a real defect that will cause a wrong result or a bad
  failure mode, but is bounded: an unhandled edge case, a missed caller of a
  changed contract, an error swallowed where it should surface.
- **`minor`** — genuinely worth someone's attention but not worth a repair
  cycle for. It is recorded and stays on the item.

If you are between two levels, take the lower one (a finding your instructions
list from last round keeps its severity unless the code changed). Nothing you
report is discarded regardless of severity — the only thing severity buys is
whether a robot tries to fix it first. In `message`, say what is wrong and why,
and leave out what Kraft does with it next: that depends on the chain, not on you.

## Finding nothing is a real answer

A clean diff is the common case, and reporting no findings is the correct
result for one. Do not manufacture a finding to look thorough: a fabricated
`important` costs a repair cycle and teaches the loop to distrust you. An empty
findings list from a review that actually ran is a pass.

## Output

Write your findings into the JSON result file at `$KRAFT_RESULT_PATH`, as a
`findings` array, alongside the `status` field you already owe the
orchestrator. Each finding is an object:

```
{ "severity": "critical" | "important" | "minor",
  "message": "what is wrong and why, in one or two sentences",
  "file": "path/relative/to/repo.py",
  "line": 42,
  "source_plugin": "code-review" }
```

A real message reads like this: "`parse_range()` returns [] past the last page and
`paginate()` then indexes [0]: page 9 of 3 raises IndexError."

`severity`, `message` and `source_plugin` are required — a finding missing any
of the three is dropped by the parser without a word, so a review that writes
them wrong reads downstream as a review that found nothing. `source_plugin` is
always `code-review`.

If your instructions list last round's findings with tags, add `same_as` to each
finding that persists, set to its tag: it is the only thing that tells the fix
loop a defect is recurring rather than new. Leave it out otherwise, and never
invent a tag: one that doesn't match is discarded. `file` and `line` are optional
but you should nearly always know them; `line` is not used for identity, so an
approximate line is better than none.

Before you finish, read the file back: every finding has `severity`, `message`
and `source_plugin`, `severity` is one of the three (any other value is dropped
the same way), and `line` is a single number.

Set `status` to `"done"` when you completed the review, whatever you found.
Reserve `"failed"` for being unable to review at all — an unreadable diff, a
missing package — and say which in your session summary: the result file has
no field for the reason, and a bare `"failed"` tells the person nothing. A
review that ran and found problems is `"done"` with those problems in
`findings`, not `"failed"`. A review that covered only part of the change (a
package cut short, a file you could not read) is `"done_with_concerns"`, with
what you did not cover in `concerns`.
