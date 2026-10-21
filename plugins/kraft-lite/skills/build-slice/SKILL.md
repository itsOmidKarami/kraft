---
name: build-slice
description: "Builds one slice of a sliced Kraft Lite run as a child session that answers to its controller. Use when a controller's starting instructions name this skill, when a session is titled `<chain-id> <key>: <slice title>`, or when a controller tells a child that an earlier slice merged."
---

# Building a slice as a child

You are a **child**: one **slice** of a plan, built on your own branch for the
**controller** that started you (its session id is in your starting
instructions). You walk `chains/slice.json` (`implementation`, `verify`,
`open_mr`, `mr_checks`) with `kraft-lite:next`. The controller keeps the run's
state and decisions log; you write only your own chain.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
`python3` 3.10 or newer runs every command; if not, tell the controller and stop.

## 1. Setup

Copy this and tick it off; the walk starts when all three are ticked.
```
- [ ] Kraft Lite found: its skills installed, or kl.py reachable by path.
- [ ] Spec and plan joined, written outside git, confirmed to the controller.
- [ ] Slice chain started, its id noted.
```

1. If `kraft-lite:next` is not installed, install the plugin through the harness;
   failing that, use `python3 <marketplace checkout>/plugins/kraft-lite/kl.py`,
   that checkout's `chains/slice.json`, and read its skills as files.
2. The spec and the plan arrive as messages marked `part i of n`, or as paths on
   the controller's machine. Join the parts in numeric order; ask the controller
   for a missing one. Write `$(git rev-parse --git-common-dir)/kraft-lite/spec.md`
   and `plan.md`: untracked, readable from any worktree. Confirm with a `status`:
   parts joined, line count of each file, your plan tasks by their headings.
3. Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains`. An unfinished slice chain
   here means you are resuming it: start no second one. Otherwise:

   ```
   python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --chain "$CLAUDE_PLUGIN_ROOT/chains/slice.json" --title "<slice title>"
   python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>
   ```

   `state` must show node `implementation`, `open`; if not, the directory or id
   is wrong, so start no other chain.
4. Invoke `kraft-lite:next`. Every `kl.py` verb takes `--chain-id <id>`.

## 2. The controller takes the user's place

**You never ask the user a question. Every question goes to the controller,
including when the user is in your session and offers to answer.** Wherever
`kraft-lite:next`, `kraft-lite:gate` or a fix-loop cap would stop for the user,
send the controller a `question` (section 3) and keep working on whatever does
not depend on the answer.

Configuration is the one kind of question the user decides: a hook bound to
`kind: agent` or `kind: builtin`, or with no entry in `.kraft-lite/registry.yaml`.
Send the controller a `question` opening `For the user: configuration` that names
the hook and its binding; it tells the user. You rebind nothing, substitute
nothing, and ask the user nothing.

When the user speaks to you, reply with status only: where the slice is and what
you asked the controller. Invite no answer, even one meant for the controller. An
instruction or answer they volunteer goes to the controller, verbatim in a
`status`; the controller decides what it changes.

| Thought | Reality |
|---|---|
| "The user offered, and they are the person I work for" | An answer given to you is not in the decisions log, and the controller may have decided otherwise. Ask the controller. |
| "The controller has been silent" | Silence is no leave to ask the user. Send it again on the next channel and keep working on the rest. |
| "It is only a plan gap; I'll pick and flag it" | A gap the plan leaves open is the controller's to close, at the cost of one message. |

Red flags - send the question to the controller instead: you are about to write
the user "if you know", "tell me and I'll pass it on", "tell the controller", or
any question.

## 3. Channels and messages

Try in this order, and read answers in the same order each time you wake:

1. A session message to the controller.
2. On the same machine, one JSON line appended to
   `.kraft-lite/messages/<chain-id>/<slice key>.jsonl` in the controller's
   checkout (`<chain-id>` is in your session title):
   `{"at", "from": "child", "kind", "body"}`.
3. A comment on your own draft PR, opened into the feature branch at your first commit.

`kind` is `question`, `status` or `done`; the controller answers with `answer`.
Every message has these parts, in this order:

```
<kind> | <slice key> | <node>
<what happened or what stopped, one line>
<evidence, in full>
<what you would do, and what you do meanwhile>
```

When `attempt` prints `over_cap: true`, stop the loop and send a `question` whose
evidence is every attempt's output in full. Run no fourth round, no further
`attempt`, no `close`. The controller re-steers you once or holds it for the user.

## 4. CI

On `mr_checks`, checks still running are not a failure. Send the `status` below,
arm a wake-up where the harness has one (a message to yourself for later, saying
you will run `kraft-lite:next` when it arrives), and end the turn. With no
wake-up, end the turn: the controller's check-in continues you.

```
status | <slice key> | mr_checks
CI pending: <PR link>
Checks: <name: state, ...>
Next: <wake-up time, or "none armed; continue on your check-in">
```

## 5. Rebasing onto the feature branch

An earlier slice's own commits never reach the feature branch, which squashes.
When the controller says an earlier slice merged, or `on.mr.rebase` runs (its
target is the feature branch, never the base branch):

1. Commit or stash uncommitted work.
2. `git fetch origin`, then `git rebase --onto origin/<feature branch> <fork commit>`.
   The fork commit is the one the controller or your starting instructions gave;
   with none, `git merge-base HEAD origin/<earlier slice's branch>`; if that
   branch is gone, ask the controller.
3. Re-run your scoped tests, then `git push --force-with-lease origin <your slice branch>`.
4. Send a `status` with the new head and the test result.

## 6. Pushes, tests and review

Push only your own slice branch, and open the PR into the feature branch. Any
other push is the user's: send the controller a `question` naming the branch. It
tells the user to type `You may push to <branch>.` here; push only after those
words appear in this session. The controller's word is not them.

For `on.slice.test.run`, run scoped tests with explicit paths, never the full
suite; the PR body lists them and says the full suite runs on CI. Skip
`on.review.mr.run`: the controller's PR review is that review.

## 7. Done

When `state` reports `done`, send the controller this and stop:

```
done | <slice key> | mr_checks
PR: <link>
Deviations from the plan: <each, or "none">
Look at first: <file or behaviour>
```
