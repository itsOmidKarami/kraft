---
name: security-review
description: Use when the verify node asks for a security review of a change that touches authentication, sessions, tokens, secrets, or permission checks. Emits findings the fix loop reads, at severities that decide whether implementation runs again.
---

# Reviewing this work item's diff for security

`chain_review` added this task because the plan touches something that decides
*who may do what* — authentication, session handling, tokens, secrets, or a
permission check. You are not repeating `on.review.local.run`'s general review;
you are reading the same diff with one question in mind, and you are allowed to
find nothing.

## What you are looking at

The diff for this work item is handed to you by path, as a review package. Read
the files it touches around the change, not just the changed lines. Almost every
real finding here lives in the interaction between new code and an assumption
the old code was already making.

## What earns a finding

A finding is a specific defect at a specific place. "Consider hardening this" is
not a finding. If you cannot name the file and say what an attacker or an
unlucky caller actually gets, you do not have one.

Look for, in rough order of what actually bites:

- **A check that does not run.** A permission or ownership test skipped on one
  path, behind a cache, after an early return, or in the error branch. The most
  common real hole is not a wrong check — it is a missing one on the second
  route to the same resource.
- **Trust drawn from the wrong place.** A caller's identity taken from a request
  body, a header the client controls, or a work item's own record rather than
  the session. Kraft's own model has a live example worth pattern-matching
  against: a worker must not approve its own gate, and that is enforced by
  `origin`, not by asking the agent.
- **Secrets crossing a boundary.** Tokens or password hashes reaching a log, an
  event payload, an error message, an unauthenticated endpoint, or the frontend.
  `/api/health` is deliberately public — anything added to it is public too.
- **Session and cookie semantics.** Expiry, renewal, revocation, `HttpOnly` /
  `SameSite` / `Secure`, and what happens to an in-flight session when the
  password or bind address changes.
- **A widened perimeter.** A new route outside the authenticated set, a
  loosened `allowed_hosts`, a bind address moving off loopback, or a CORS or
  proxy rule that lets an origin in. A LAN bind without a password is a stop.
- **Injection into something that executes.** A shell command, a SQL string, a
  path joined from caller input, or a file written outside the worktree.

Out of scope: the general correctness review the sibling task already does, and
advice that does not bind to this diff ("add rate limiting everywhere"). If the
change is security-relevant but sound, say so and emit no findings.

## Severity

Severity decides whether the fix loop re-runs implementation — by default
`critical` and `important` reopen it, `minor` does not.

- **`critical`** — exploitable as written, or a secret is already leaking. An
  unauthenticated path to authenticated data, a permission check that can be
  skipped, a token in a log.
- **`important`** — a real weakening that needs fixing before this merges, but
  needs a precondition an attacker does not have yet: a session that outlives a
  revocation, a check correct here but bypassable by the next caller of the same
  helper.
- **`minor`** — worth a human's attention, not worth a paid re-run. Defence in
  depth, a comment that misstates the guarantee.

Do not inflate. An `important` costs a full implementation re-run; spending one
on a theoretical concern teaches the loop that this review is noise.

## Output

Write your findings into the JSON result file at `$KRAFT_RESULT_PATH`, as a
`findings` array, alongside the `status` field you already owe the orchestrator:

```
{ "severity": "critical" | "important" | "minor",
  "message": "what is wrong and why, in one or two sentences",
  "file": "path/relative/to/repo.py",
  "line": 42,
  "source_plugin": "security-review",
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
were not shown: one that does not match is discarded.

Set `status` to `"done"` when you completed the review, whatever you found.
Reserve `"failed"` for being unable to review at all — an unreadable diff, a
missing package. A review that ran and found problems is `"done"` with those
problems in `findings`.
