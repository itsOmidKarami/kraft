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
user and stop. In prose below, `KL` is short for `python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`:
type the full command; the copyable blocks spell it out. Every verb takes
`--chain-id <id>` except `settings`, `import`, `chains`, `detect` and `start`.

The tools for each ability are in your harness's reference; read it before
section 2: [Claude Code](references/claude-code.md),
[Codex](references/codex.md), [Cursor](references/cursor.md).

## 1. When to slice

At `chain_review`, after its bound `on.chain.review_ready` hook. A small plan is
not sliced: say so, delete any `.kraft-lite/proposals/<chain-id>.json` left by a
rejected round, then `KL gate --name chain_finalized` and invoke
`kraft-lite:gate`; the chain continues as one.

1. Read the plan's `## Slices` section (bullets like `- **Slice 2: <title>.**
   Tasks 3–5. Comes after slice 1.`). With none, propose a grouping.
2. Draw the proposal: each slice's tasks, what it comes after, which slices run
   in parallel, and one owning slice for every shared helper, fixture or file
   two slices would both create. The other slice waits for it or uses it from
   the owner's branch.
3. Add the mode: the user's words for this run if they named one, logged with
   `KL decide --question "Mode for this run" --answer "<their words>" --why "set by the user"`,
   else the mode `KL settings` prints. Add the feature branch (`feature` mode: a
   new branch from the base branch, named by the repo's own rules; `main` mode:
   `null`) and the state branch (section 7). The feature branch is never the
   state branch.
4. Write it to `.kraft-lite/proposals/<chain-id>.json`:
   `{"mode", "feature_branch", "state_branch", "slices": [{"key", "title", "tasks": [N], "after": [key]}]}`.
5. `KL gate --name chain_finalized`, then present the proposal as that gate's
   artefact and end your turn. The user's answer is the approval.
6. On approval: `KL approve --slices .kraft-lite/proposals/<chain-id>.json`. On
   a rejection, `kraft-lite:gate` handles it and deletes the proposal; no slice
   records exist yet. A chain rewound past `chain_finalized` cannot be sliced
   again.

## 2. Before the first child

Copy this and tick it off; while a line is unticked, no child starts.

```
- [ ] Abilities confirmed from the harness reference, or its fallback named: start, message, be messaged, archive, check in later. Deferred tools searched before calling one missing. A missing ability is never a block.
- [ ] One user approval for every branch this run pushes outside this session's own: the feature branch (`feature` mode), and locally the state branch.
- [ ] Setup and the scoped test command run once here, and the working setup line written down.
- [ ] The user has finished giving instructions for this run: ask, and hear "go".
- [ ] Rights read (section 12), and any the user changed for this run logged with `decide`.
- [ ] `feature` mode: feature branch pushed. State exported (section 7).
```

## 3. Starting a child

A slice's **target** is the branch its PR goes into: the feature branch, or in
`main` mode the base branch. A slice is cut from the target's tip, or from an
earlier slice's branch: started early during that slice's final review, or in
`main` mode after a slice it comes after is ready to merge but not merged, since
that work is on its branch alone.

For each key in `KL slices`'s `ready`:

1. `KL slice-set --slice <key> --status starting --branch <slice-branch> --fork <sha>`,
   where `<sha>` is the commit it is cut from (`git rev-parse origin/<branch>`).
   Export state (section 7).
2. Start the session from that commit. If the user named a session kind for
   this run, use it; otherwise the same kind as this session. Its own branch is
   the slice branch; its title is `<chain-id> <key>: <slice title>`; it is
   tagged with the chain id where the reference confirms tags; it has **an
   explicit model** (middle tier).
3. `KL slice-set --slice <key> --status running --session <id> --branch <slice-branch>`.
   Cut from another slice's branch, also
   `KL decide --slice <key> --question "Start <key> from <slice>'s branch?" --answer "Forked from <branch> at <sha>" --why "..."`.
   Export state.
4. Have a cheapest-tier subagent send the spec and the plan, each in parts
   marked `part i of n` under the message limit. On the same machine, send their
   paths instead.
5. Arm the check-in if none is armed (section 5).

A replacement child for a slice whose branch already holds work (a resume)
continues that branch. Its `Cut from:` line carries the fork `KL slices`
records: `target` once the old child reported a rebase, else as first given.
On the same machine, delete the old child's folder first,
`$(git rev-parse --path-format=absolute --git-common-dir)/kraft-lite/<chain-id>/<key>/`,
so the new child writes its own `fork`, `base` and `chain` files.

The starting instructions, in this order:

1. **Slice:** the feature chain's id, then key, title, its plan tasks, what it
   comes after, which slices run beside it, what it owns or must take from an
   owner's branch, and the line `Cut from: <target | slice key> at <sha>`, the
   `--fork` sha: `target` when cut from the target, else the key of the slice
   whose branch it was cut from.
2. **Controller:** this session's id, and locally its name too (a local session
   message is addressed by name). The channels in the order to try them: a
   session message; on the same machine
   `.kraft-lite/messages/<chain-id>/<key>.jsonl` under this checkout's absolute
   path (the feature chain's id; one JSON per line: `at`, `from`, `kind`:
   question|answer|status|done, `body`); else comments on its own draft PR into
   its target, opened at its first commit.
3. **Kraft Lite:** invoke `kraft-lite:build-slice`; if the plugin is missing,
   install it through the harness, else run
   `python3 <marketplace checkout>/plugins/kraft-lite/kl.py` and read the skills
   as files there.
4. **Documents:** the spec and plan arrive next in parts marked `part i of n` (or
   as paths on the same machine); join them, write them down, and confirm.
5. **Setup:** the working setup line from section 2.
6. **Method:** one subagent per task, TDD, a spec-compliance review and a
   code-quality review per task, a capped fix round, parallel tasks in isolated
   worktrees. Implementation stops after the last task's reviews: its one
   whole-branch review is its `on.review.local.run`. It skips
   `on.review.mr.run`: your PR review is that review.
7. **Models:** the policy in section 11, with the model set on every subagent.
8. **Tests:** scoped tests only, explicit paths, for `on.slice.test.run`; the full
   suite runs on CI. The PR body lists the paths run.
9. **Branches and PRs:** push only its own slice branch; its target, named:
   the feature branch, or in `main` mode the base branch. Its PR goes into the
   target, and so does `on.mr.rebase`. It rebases as `kraft-lite:build-slice`
   says, with `git rebase --onto origin/<target> <fork commit>`, and
   force-pushes its own branch. The repo's rules, quoted from its `CLAUDE.md`
   and `CONTRIBUTING.md`.
10. **Rights:** what it may decide alone, and that every other question comes to
    the controller, never the user; keep working on whatever does not depend on
    a pending answer.
11. **Done message:** PR link, deviations from the plan, what to look at first.

### In `main` mode

There is no feature branch: nothing is pushed for one, and every slice PR goes
to the base branch. A slice whose `after` slices are ready to merge is in
`ready`, so start it then, from the branch of the one it builds on, without
waiting for the user's merge. When the user merges a slice, the steps after a
merge (section 6) apply, with the base branch as the target.

## 4. Resuming

Before any other write and before starting anything:

1. Fetch the state branch, `KL import --from <state-checkout>/kraft-lite-state`,
   then `KL chains` for the chain id. An empty `chains` before the import is a
   missing import: never `start` a new chain.
2. `KL slices`. For every `starting` or `running` slice, and every `waiting`
   one in `ready`, find its session, in the ways the reference confirms: the
   recorded session id; a session whose title or name carries `<chain-id>` and
   `<key>`; the chain-id tag; and its slice branch on the remote
   (`git ls-remote origin <slice-branch>`) as evidence a child ran.
   - Found: `KL slice-set --slice <key> --status running --session <id> --branch <slice-branch>`.
   - Not found, and `starting`, `running`, or its slice branch on the remote:
     never start it blindly. Hold it with
     `KL decide --held --slice <key> --question "No session found for <key>: start a fresh child?" --why "starting a session twice duplicates the work"`,
     tell the user what you found (a pushed branch, an archived session), and
     start one only on their answer.
   - Not found, `waiting` in `ready`, and no slice branch on the remote: start
     it (section 3).
3. Export state, arm the check-in (section 5), then report (section 8).

## 5. Following up

A check-in is armed while any slice is not merged; in `main` mode, while
`implementation`, `human_review` or `merge` is open. Its message, word for word:
`kraft-lite:run-slices check-in, chain <id>: read state first.` Every check-in:

```
- [ ] `KL state` and `KL slices`. Past `implementation`: invoke `kraft-lite:next`, which records the user's merges, and skip to the last line.
- [ ] Read messages, then `.kraft-lite/messages/<chain-id>/`, then each slice PR's comments.
- [ ] Per running child: its status summary (fields in the reference), and `git fetch` + `git log` of its branch. Never its full transcript.
- [ ] A child shown as blocked: find out on what (often its own subagents) before telling the user anything.
- [ ] `main` mode: each `ready_to_merge` slice's PR. One the user merged takes the steps after a merge (section 6).
- [ ] Answer every question (below). Export state.
- [ ] A child that is idle after reporting CI pending, or after a held answer: message it to continue.
- [ ] Re-arm the check-in while its condition holds.
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
  its question opens `For the user: configuration`, and it goes to the user.
- A child reports a rebase with a new `Cut from:` line:
  `KL slice-set --slice <key> --fork <sha>`, then export state.

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
4. Only when the user has answered: `KL answer --decision <id> --answer "<their words>"`,
   then send their answer to the child (a permission: tell the child to look
   for the words in its own session).

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

Starting a review: `KL slice-set --slice <key> --status in_review`. Your review
is the slice's MR review, on the strongest tier. Rely on CI and the child's
reported scoped run; run checks only for what CI cannot cover. A reviewer
subagent never runs `tsc` or `npm` through a shared `node_modules`.

- Findings to fix: send them to the child, `KL slice-set --slice <key> --status running`,
  and review again when it reports the fix.
- `feature` mode: merge into the feature branch only on green CI at the exact
  head you reviewed (pin the head SHA); a head that moved, read the new commits.
- `main` mode: report the verdict to the user, then
  `KL slice-set --slice <key> --status ready_to_merge`. The user merges it.
  Start what `KL slices` now lists in `ready`, and export state.

After a merge (yours, or in `main` mode the user's), in order:

1. `KL slice-set --slice <key> --status merged`
2. Tell each slice cut from this slice's branch, and each slice cut from the
   target, to rebase onto the target. Never one cut from another slice that is
   still unmerged: it rebases when that slice merges. The `Cut from:` line you
   gave each child, and its fork decision, say which it is.
3. Archive the child.
4. Start what `KL slices` now lists in `ready`.
5. Export state.

A call overturned later reopens the archived child, or starts a fresh one on the
target. When every slice is done:

1. Show the whole decisions log, `KL decisions`, while a call is cheap to overturn.
2. `KL decisions --unreported --mark`, to move the report cursor past it.
3. `KL close`, and export state. The rest of the feature chain then runs per
   `state.sliced.action`, through `kraft-lite:next`. In `feature` mode its hooks
   run in a worktree of the feature branch, never in the state branch's
   checkout.

## 7. State branch

In a cloud session the state branch is this session's own branch, and this
checkout is its checkout. Locally it is the feature branch's name plus `-state`
(in `main` mode, a name for the run plus `-state`), in its own worktree. After every change to slices or decisions, check that
`git -C <state-checkout> branch --show-current` prints the state branch (switch
to it if not; never commit state on another branch), then:

```
python3 "$CLAUDE_PLUGIN_ROOT/kl.py" export --to <state-checkout>/kraft-lite-state --chain-id <id>
git -C <state-checkout> add kraft-lite-state
git -C <state-checkout> commit -m "kraft-lite state"
git -C <state-checkout> push origin HEAD:<state-branch>
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
done): a cloud proxy refuses deletes. In `feature` mode the feature PR is the
chain's `open_mr`.

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
