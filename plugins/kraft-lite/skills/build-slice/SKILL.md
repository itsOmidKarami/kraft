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

Your **target** is the branch your PR goes into, as the **Branches and PRs**
item of your starting instructions names it: the feature branch, or in `main`
mode the base branch.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
`python3` 3.10 or newer runs every command; if not, tell the controller and stop.

## 1. Setup

Copy this and tick it off; the walk starts when all four are ticked.

```
- [ ] Kraft Lite found: its skills installed, or kl.py reachable by path.
- [ ] Spec and plan joined, written outside git, confirmed to the controller.
- [ ] Fork and base files written, before any commit of your own.
- [ ] Slice chain started or resumed, its id in the chain file.
```

1. If `kraft-lite:next` is not installed, install the plugin through the harness;
   failing that, use `python3 <marketplace checkout>/plugins/kraft-lite/kl.py`,
   that checkout's `chains/slice.json`, and read its skills as files.
2. The spec and the plan arrive as messages marked `part i of n`, or as paths on
   the controller's machine. Join the parts in numeric order; ask the controller
   for a missing one. Write `<run>/spec.md` and `plan.md`, where `<run>` is
   `$(git rev-parse --path-format=absolute --git-common-dir)/kraft-lite/<chain-id>`
   and `<chain-id>` is the feature chain's, named in the **Slice** item of your
   starting instructions. Untracked, readable from any worktree, and the same
   for every slice of the run, so slices sharing a repo write the same files.
   Confirm with a `status`: parts joined, line count of each file, your plan
   tasks by their headings.
3. Record where your branch was cut, for section 5. The **Slice** item's line
   gives both: `Cut from: target at <sha>` from the target's tip, where `target`
   is the word itself, never a branch name, or `Cut from: <slice key> at <sha>`
   from that slice's branch. In `<run>/<slice key>/` (yours alone, as worktree
   slices share the repo), write each file only if absent, so a resume keeps it:

   ```
   [ -f <run>/<slice key>/fork ] || echo <sha> > <run>/<slice key>/fork
   [ -f <run>/<slice key>/base ] || echo <the word after "Cut from:", as given> > <run>/<slice key>/base
   ```

   `base` holds the word `target` or a slice key, never a branch name. With no
   `Cut from:` line and no commits of your own, write `target` and
   `git rev-parse HEAD`; with commits of your own, never `HEAD`: ask the
   controller for the line.
4. Your slice chain's id lives in `<run>/<slice key>/chain`, never in the
   `chains` list, which shows every chain in a shared store, siblings included.
   If the file holds an id, resume that chain. If it is absent:

   ```
   python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --chain "$CLAUDE_PLUGIN_ROOT/chains/slice.json" --title "<slice title>"
   echo <id> > <run>/<slice key>/chain
   python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>
   ```

   `state` must show node `implementation`, `open`; if not, the directory or id
   is wrong, so start no other chain.
5. Invoke `kraft-lite:next`. Pass your slice chain's `--chain-id <id>` to every
   verb but `start`.

### A fresh container

`<run>/<slice key>/` is missing while your branch on the remote already has
commits of your own: the container was replaced, and the chain state and the
documents went with it. The pushed commits are the work done.

1. Put your branch at its pushed head before anything else:
   `git fetch origin`, then `git checkout -B <your slice branch> origin/<your slice branch>`.
   A fresh clone can check out an older revision, and the fork checks and
   `--force-with-lease` would then not see the pushed work.
2. Send the controller a `status`: fresh container, your pushed head and PR.
   Ask it for the spec and the plan again.
3. Run setup. `fork` and `base` come from the latest `Cut from:` line here (your
   starting instructions or your last rebase `status`), never from `HEAD`.
4. At the new chain's `implementation`, build only the plan tasks your pushed
   commits do not hold. Never redo, reset or force-push away pushed work.

## 2. The controller takes the user's place

**You never put a question to the user. Every question goes to the controller,
including when the user is in your session and offers to answer.** Wherever
`kraft-lite:next`, `kraft-lite:gate` or a fix-loop cap would stop for the user,
send the controller a `question` (section 3) and keep working on whatever does
not depend on the answer.

Configuration is the one kind of question the user decides: a hook bound to
`kind: agent` or `kind: builtin`, or with no entry in `.kraft-lite/registry.yaml`.
Send the controller a `question` opening `For the user: configuration` that names
the hook and its binding; it tells the user. You rebind nothing, substitute
nothing, and put no question to the user.

When the user speaks to you, reply with status only: where the slice is and what
you asked the controller. Invite no answer, even one meant for the controller. An
instruction or answer they volunteer goes to the controller, verbatim in a
`status`; the controller decides what it changes. When the user types
`You may push to <branch>.` for a push you asked the controller about, that is
the permission: push, then send the controller a `status`.

| Thought | Reality |
|---|---|
| "The user offered, and they are the person I work for" | An answer given to you is not in the decisions log, and the controller may have decided otherwise. Ask the controller. |
| "The controller has been silent" | Silence is no leave to put it to the user. Send it again on the next channel and keep working on the rest. |
| "I'll just say: if you want to steer it, tell the controller" | That is still an invitation to answer. Say where the slice is and that you asked, and stop. |
| "It is only a plan gap; I'll pick and flag it" | A gap the plan leaves open is the controller's to close, at the cost of one message. |

Red flags - send the question to the controller instead: you are about to write
the user "if you know", "tell me and I'll pass it on", "tell the controller", or
any question.

## 3. Channels and messages

Try in this order, and read answers in the same order each time you wake:

1. A session message to the controller (locally, addressed by the name your
   starting instructions give).
2. On the same machine, one JSON line appended to
   `.kraft-lite/messages/<chain-id>/<slice key>.jsonl` under the controller's
   checkout path in your starting instructions: `{"at", "from": "child", "kind",
   "body"}`, where `<chain-id>` is the feature chain's, never your slice chain's.
3. A comment on your own draft PR, opened into the target at your first commit.

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
`attempt`, no `close` until the controller answers; then follow its steer. It
re-steers you once or holds the question for the user.

## 4. CI

On `mr_checks`, checks still running are not a failure. Send the `status` below,
arm a wake-up (the scheduling tool your **Controller** item names: a message to
yourself for later, saying you will run `kraft-lite:next` when it arrives), and
end the turn. Where it names none, end the turn: the controller's check-in
messages you to continue. Ask no one to run a skill or a command for you, the
user included.

```
status | <slice key> | mr_checks
CI pending: <PR link>
Checks: <name: state, ...>
Next: <wake-up time, or "none armed; continue on the controller's check-in">
```

## 5. Rebasing onto the target

The target squash-merges, so an earlier slice's own commits never reach it.
`<run>/<slice key>/base` says when you rebase:

| `base` holds | Rebase when | When `on.mr.rebase` runs |
|---|---|---|
| `target` | `on.mr.rebase` runs, or the controller says to | rebase |
| a slice key | the controller says that slice merged | nothing to do: that slice's commits are on your branch and not yet on the target, so a rebase would drop them |

To rebase:

1. Commit uncommitted work, or stash it and pop it after the rebase.
2. `git fetch origin`, then check the fork. If either check fails, run no
   rebase: send the controller a `question` with both shas, and keep working.
   - `git merge-base --is-ancestor "$(cat <run>/<slice key>/fork)" HEAD` succeeds.
   - The fork is not `HEAD` itself while your branch has commits of your own.
3. `git rebase --onto origin/<target branch> "$(cat <run>/<slice key>/fork)"`. That
   commit is where your branch was cut, so this works after a start from a
   slice branch that was since force-pushed or deleted. Once it succeeds, your
   branch is cut from the target, so record that:
   `git rev-parse origin/<target branch> > <run>/<slice key>/fork` and
   `echo target > <run>/<slice key>/base` (the word `target`).
4. Re-run your scoped tests, then `git push --force-with-lease origin <your slice branch>`.
5. Send a `status` with the new head, the test result, and the line
   `Cut from: target at <the new fork>`.

## 6. Pushes, tests and review

Push only your own slice branch, and open the PR into the target. Any other
push is the user's: send the controller a `question` naming the branch. It
tells the user to type `You may push to <branch>.` here; push only after those
words appear in this session. The controller's word is not them.

`on.slice.implementation.start` ends after the last task's own reviews, whatever
it is bound to: no final whole-branch review and no finishing, merge or PR step
inside it, and no question to the user about one. `on.review.local.run` is the
slice's one whole-branch review, and `open_mr` opens the PR. When the bound
skill offers either, close `implementation` instead.

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
