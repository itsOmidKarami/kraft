---
name: security-review
description: "Reviews the diff a work item has produced for authentication, session, token, secret, permission-check and injection defects, and writes severity-ranked findings to the result file. Kraft runs it as an extra review task beside the general code review in a verification node. No shipped chain runs it: an operator adds it to the library and a chain, for changes that touch those areas."
---

# Reviewing this work item's diff for security

This task is in the chain because the work touches something that decides
*who may do what* — authentication, session handling, tokens, secrets, or a
permission check. You are not repeating the general code review beside you;
you are reading the same diff with one question in mind, and finding nothing is
a valid result.

The tests passed in the step before yours: don't run them, and don't change,
add or commit anything in the worktree, whatever the instructions above say
about committing. Confirm a defect by reading the code; a repair agent acts on
your findings.

## What you are looking at

The diff for this work item is handed to you by path, as a review package. Read
it first; its context lines already show the changed files. Almost every real
finding here lives in the interaction between new code and an assumption the old
code was already making, so read that code too — callers, neighbours, whatever
the package does not show.

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
  body, a header the client controls, or a field on the object being acted on
  rather than the session, so an actor can vouch for itself by asserting who it
  is.
- **Secrets crossing a boundary.** Tokens or password hashes reaching a log, an
  event payload, an error message, an unauthenticated endpoint, or a response
  sent to the browser. Anything added to a deliberately public endpoint is
  public too.
- **Session and cookie semantics.** Expiry, renewal, revocation, `HttpOnly` /
  `SameSite` / `Secure`, and what happens to an in-flight session when the
  password or the user's role changes.
- **Tokens and secrets that aren't checked.** A signature, audience or expiry
  not verified, a compare that isn't constant-time, a predictable token, a
  secret hard-coded or committed, TLS verification turned off.
- **A widened perimeter.** A new route outside the authenticated set, a
  loosened host allowlist, a bind address moving off loopback, or a CORS or
  proxy rule that lets an origin in. A service newly reachable beyond loopback
  with no authentication in front of it is `critical`; binding every interface
  inside a container or behind a proxy isn't a finding by itself.
- **Injection into something that executes.** A shell command, a SQL string, a
  path joined from caller input, or a file written outside the directory it is
  meant for.

Out of scope: general correctness, which is the code review's where the chain
has one, and advice that does not bind to this diff ("add rate limiting
everywhere"). A defect that was already there, and that this diff neither
touches nor relies on, isn't a finding: note it in your session summary. If the
change is security-relevant but sound, say so in your session summary and emit
no findings.

## Severity

Severity decides whether a fix cycle opens — by default `critical` and
`important` open one, `minor` does not.

- **`critical`** — exploitable as written, or a secret is already leaking. An
  unauthenticated path to authenticated data, a permission check that can be
  skipped, a token in a log.
- **`important`** — a real weakening that needs fixing before this merges, but
  needs a precondition an attacker does not have yet: a session that outlives a
  revocation, a check correct here but bypassable by the next caller of the same
  helper.
- **`minor`** — worth a human's attention, not worth a paid re-run. Defence in
  depth, a comment that misstates the guarantee.

It's `critical` only if you can state the call that exploits it from this diff
as written; if it needs something the diff doesn't add, it's `important`.
Between two levels, take the lower one. An `important` costs a fix cycle and a
second review; spending one on a theoretical concern teaches the loop that this
review is noise.

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
of the three, or with a `severity` outside the three values, is dropped by the
parser without a word, so a review that writes them wrong reads downstream as a
review that found nothing. `file` and `line` are optional, but name them when
you know them; `line` is not part of a finding's identity.
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
missing package — and say which in your session summary: the result file has
no field for the reason. A review that ran and found problems is `"done"` with
those problems in `findings`; a clean one is `"done"` with `"findings": []`.

Before you finish, read the file back: it parses, and every finding has
`severity`, `message` and `source_plugin`.
