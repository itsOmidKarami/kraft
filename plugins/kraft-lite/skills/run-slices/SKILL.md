---
name: run-slices
description: "Controls a sliced Kraft Lite run, in which one feature chain's plan is built by several child sessions, one per slice. Use when a Kraft Lite `state` reports `sliced.action: run_slices`, when a large plan at `chain_review` should be split across sessions, or when resuming, checking on, or reporting a sliced run."
---

# Running slices as the controller

You are the **controller**. Each **slice** is one or more plan tasks, built by a
**child** session that walks `chains/slice.json`. Children report to you; you
answer them, review and merge their work into the **feature branch**, and keep
the run's truth in `.kraft-lite/`, copied to the **state branch** after every
change. Your conversation is not state: after a compaction or a restart, read
`state`, `slices` and `decisions` again.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
`python3` 3.10 or newer runs every command; if it is missing or older, tell the
user and stop. Below, `KL` is short for `python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`:
type the full command, with `--chain-id <id>` on every chain verb.

The tools for each ability are in your harness's reference; read it before
section 2: [Claude Code](references/claude-code.md),
[Codex](references/codex.md), [Cursor](references/cursor.md).

## 1. When to slice

At `chain_review`, after its bound `on.chain.review_ready` hook. A small plan is
not sliced: say so, and the chain continues as one.

1. Read the plan's `## Slices` section (bullets like `- **Slice 2: <title>.**
   Tasks 3–5. Comes after slice 1.`). With none, propose a grouping.
2. Draw the proposal: each slice's tasks, what it comes after, which slices run
   in parallel, and one owning slice for every shared helper, fixture or file
   two slices would both create. The other slice waits for it or uses it from
   the owner's branch.
3. Add the mode (`KL settings`), the feature branch (a new branch from the base
   branch, named by the repo's own rules) and the state branch (section 7). The
   feature branch is never the state branch.
4. Write it to `.kraft-lite/proposals/<chain-id>.json`:
   `{"mode", "feature_branch", "state_branch", "slices": [{"key", "title", "tasks": [N], "after": [key]}]}`.
5. `KL gate --name chain_finalized`, then present the proposal as that gate's
   artefact and end your turn. The user's answer is the approval.
6. On approval: `KL approve --slices .kraft-lite/proposals/<chain-id>.json`. On
   a rejection, `kraft-lite:gate` handles it; no slice records exist yet. A chain
   rewound past `chain_finalized` cannot be sliced again.

## 2. Before the first child

Copy this and tick it off; while a line is unticked, no child starts.

```
- [ ] Abilities confirmed from the harness reference: start, message, be messaged, archive, check in later. Deferred tools searched before calling one missing.
- [ ] One user approval for every branch this run pushes outside this session's own: the feature branch, and locally the state branch.
- [ ] Setup and the scoped test command run once here, and the working setup line written down.
- [ ] The user has finished giving instructions for this run: ask, and hear "go".
- [ ] Rights read (section 12), and any the user changed for this run logged with `decide`.
- [ ] Feature branch pushed; state exported (section 7).
```

## 3. Starting a child

For each key in `KL slices`'s `ready`:

1. `KL slice-set --slice <key> --status starting`
2. Start the session: the same kind as this one (cloud or local) unless the user
   said otherwise; its own branch is the slice branch; tagged with the chain id
   where the harness has tags; **an explicit model** (middle tier).
3. `KL slice-set --slice <key> --status running --session <id> --branch <slice-branch>`.
   Starting early from an earlier slice's branch, during its final review, adds
   `--fork <sha>` and a `KL decide --slice <key> --question "Start <key> early?" --answer "Forked from <branch> at <sha>" --why "..."`.
4. Export state (section 7).

The starting instructions, in this order:

1. **Slice:** key, title, its plan tasks, what it comes after, which slices run
   beside it, what it owns or must take from an owner's branch.
2. **Controller:** this session's id, and the channels in the order to try them:
   a session message; on the same machine
   `.kraft-lite/messages/<chain-id>/<key>.jsonl` in this checkout (one JSON per
   line: `at`, `from`, `kind`: question|answer|status|done, `body`); else
   comments on its own draft PR into the feature branch, opened at its first
   commit.
3. **Kraft Lite:** invoke `kraft-lite:build-slice`; if the plugin is missing,
   install it through the harness, else run `kl.py` by path from the
   marketplace repo and read the skills as files.
4. **Documents:** the spec and plan arrive next in numbered parts, from a
   cheapest-tier subagent you send under the message limit; it joins them and
   confirms. On the same machine, their paths instead.
5. **Setup:** the working setup line from section 2.
6. **Method:** one subagent per task, TDD, a spec-compliance review and a
   code-quality review per task, a capped fix round, parallel tasks in isolated
   worktrees, one whole-branch review as its `on.review.local.run`. It skips
   `on.review.mr.run`: your PR review is that review.
7. **Models:** the policy in section 11, with the model set on every subagent.
8. **Tests:** scoped tests only, explicit paths, for `on.slice.test.run`; the full
   suite runs on CI. The PR body lists the paths run.
9. **Branches and PRs:** push only its own slice branch; the PR goes into the
   feature branch; `on.mr.rebase`'s target is the feature branch; when told an
   earlier slice merged, `git rebase --onto origin/<feature> <fork commit>` and
   force-push its own branch. The repo's rules, quoted from its `CLAUDE.md` and
   `CONTRIBUTING.md`.
10. **Rights:** what it may decide alone, and that every other question comes to
    the controller, never the user; keep working on whatever does not depend on
    a pending answer.
11. **Done message:** PR link, deviations from the plan, what to look at first.

## 4. Resuming

Before any other write and before starting anything:

1. Fetch the state branch and `KL import --from <state-checkout>/kraft-lite-state`.
   An empty `chains` before it is a missing import: never `start` a new chain.
2. `KL slices`, then match every `starting` or `running` slice to a session that
   exists (by tag, or the recorded session id). Found: record it with
   `slice-set --session`. Not found and `starting`: start it (section 3). A
   `running` slice whose session is gone: tell the user before replacing it.
3. Arm the check-in (section 5), then report (section 8).

## 5. Following up

While any child runs, a check-in is armed. Every check-in:

```
- [ ] Read messages, then `.kraft-lite/messages/<chain-id>/`, then each slice PR's comments.
- [ ] Per running child: its status summary, and `git fetch` + `git log` of its branch. Never its full transcript.
- [ ] A child shown as blocked: find out on what (often its own subagents) before telling the user anything.
- [ ] Answer every question (below). Export state.
- [ ] Re-arm the check-in.
```

Answering a question:

- A plan question, or anything else your rights cover: decide, tell the child,
  and `KL decide --slice <key> --question "..." --answer "..." --why "..."`.
  One the plan leaves open is still yours: the log keeps it cheap to overturn.
- Critical or costly to undo: `KL decide --held --slice <key> --question "..." --why "..."`,
  and tell the child to keep working on everything else.
- A user-only decision: see below.
- A fix loop over its cap arrives with its traces: re-steer the child once, and
  log it with `decide`, or hold it for the user. A child's hook bound to
  `kind: agent` or `kind: builtin`, or with no registry entry, is configuration:
  it goes to the user.

### User-only decisions, and silence

**You never answer a question that belongs to the user, and nothing but the
user's own answer answers it.** A push outside the child's own branch, merging
to the base branch, and anything `settings` gives the user, are theirs. In a
cloud session, a push the child was not given can only be approved by the user,
inside that child's own session: your "authorized" is refused there.

1. `KL decide --held --slice <key> --question "..." --why "user-only: <right>"`.
2. Tell the user the exact words and where to type them. A permission only the
   child's session can grant, such as a push: "Open session `<id>` and type:
   `You may push to <branch>.`" Any other answer: to you, in this session.
3. Tell the child it is held, and to keep working on what does not depend on it.
4. Only when the user has answered: `KL answer --decision <id> --answer "<their words>"`.

| Thought | Reality |
|---|---|
| "They said keep things moving, don't block on me" | That is how to run the rest, not an answer to this. It stays held. |
| "No reply for hours, so they're fine with it" | Silence is never an answer. A held question stays held. |
| "I'll authorize it so the child can finish" | Your approval does not reach the child's session. It is refused there. |
| "Tell me if you want to approve it" | Wrong door: name the session and the words to type in it. |
| "They said yes to me; I'll relay it and have the child retry" | A relayed yes is refused too. Record it, and send the user to the child's session. The child does not retry until the words appear there. |
| "It's obviously what they'd choose" | Then it costs them one line. Ask. |

Red flags - stop and hold the question: you are about to type "I've authorized",
"the user approved, go ahead", "assuming yes", "proceeding unless", or to run
`answer` without the user's words in front of you.

## 6. Reviewing and merging

Your review is the slice's MR review, on the strongest tier. Rely on CI and the
child's reported scoped run; run checks only for what CI cannot cover. A
reviewer subagent never runs `tsc` or `npm` through a shared `node_modules`.

- `feature` mode: merge into the feature branch only on green CI at the exact
  head you reviewed (pin the head SHA); a head that moved, read the new commits.
- `main` mode: report the verdict to the user, then
  `KL slice-set --slice <key> --status ready_to_merge`.

After a merge, in order:

1. `KL slice-set --slice <key> --status merged`
2. Tell every later slice to rebase onto the feature branch.
3. Archive the child.
4. Start what `KL slices` now lists in `ready`.
5. Export state.

A call overturned later reopens the archived child, or starts a fresh one on the
feature branch. When every slice is done, show the decisions log
(`KL decisions --unreported --mark`), then `KL close`.

## 7. State branch

In a cloud session the state branch is this session's own branch, and this
checkout is its checkout. Locally it is the feature branch's name plus `-state`,
in its own worktree. After every change to slices or decisions:

```
KL export --to <state-checkout>/kraft-lite-state
git -C <state-checkout> add kraft-lite-state && git -C <state-checkout> commit -m "kraft-lite state" && git -C <state-checkout> push
```

It holds state only (no product code, specs or plans) and is never merged.

## 8. Status

Run `KL decisions --held`, then `KL decisions --unreported --mark` (`--mark` is
refused with `--held`), and report in this shape:

```
Held for you (first, every time until answered):
- <slice>: <question> - <why held>. To answer: <exact words, and where>
Slices:
- <key> <title>: waiting on <keys> | running (<session>) | in review | ready to merge | merged
Next: <what starts or merges next>
Decided since the last report:
- <slice>: <question> -> <answer> (<why>)
Needs from you: <each action, with the session and the words>
```

## 9. Wrap-up

Give the user the branches safe to delete (merged slices; the state branch once
done): a cloud proxy refuses deletes. The feature PR is the chain's `open_mr`.

## 10. No session tools at all

If the abilities are really missing after searching deferred tools, say so, and
walk the slices here, one after another, in `ready` order, with the same review,
merge and state steps.

## 11. Models

Set the model on every child and every subagent:

- cheapest: search, reading code, gathering context, relaying documents;
- middle: child sessions, well-specified implementation, spec-compliance
  review, fixes a reviewer spelled out;
- strongest: intricate logic, a slice's whole-branch review, your PR reviews
  and judgment calls.

## 12. Rights

`KL settings` gives the mode and who decides what. The user's words at the start
of a run override it for that run; record each change with
`KL decide --question "Rights for this run" --answer "<their words>" --why "set by the user at start"`.
