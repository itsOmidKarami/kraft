---
name: run-slices
description: "Controls a sliced Kraft Lite run as its controller, with one child session building each slice of the plan. Use when `kraft-lite:next` reaches a Kraft Lite chain's slice gate (`state.gate.slice_gate` true, `sliced` null), when `state` reports `sliced.action: run_slices`, when a large plan should be split across sessions, or when resuming or checking in on a sliced run."
---

# Running slices as the controller

If `$CLAUDE_PLUGIN_ROOT` is unset, `kl.py` is two directories above this file;
it needs `python3` 3.10 or newer, and without one, tell the user and stop.

You are the **controller**. Each **slice** is one or more plan tasks, built by a
**child** session that walks the **child chain** (the nodes `kl.py` gives a
child from this run's frozen chain, as `kraft-lite:build-slice` lists them).
Children report to you; you answer them, review and merge their work into the
**feature branch**, and keep the run's truth in `.kraft-lite/`, copied to the
**state branch** after every change. Your conversation is not state: after a
compaction or a restart, read `state`, `slices` and `decisions` again.

In prose and checklists below, `KL` is short for
`python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`, and a `KL` command carries `--chain-id <id>`
even where it is not written: pass it to every verb except `settings`, `import`,
`chains`, `detect`, `migrate` and `start`. Type each command in full.

The tools for each ability are in your harness's guide; read it before
section 2: [Claude Code](references/claude-code.md), [Codex](references/codex.md),
[Cursor](references/cursor.md), [Gemini CLI](references/gemini.md),
[Antigravity](references/antigravity.md), [Amp](references/amp.md),
[OpenCode](references/opencode.md). With no guide for this harness, or an
ability its guide marks unconfirmed, follow [discover.md](references/discover.md)
before any fallback.

## 1. When to slice

At the **slice gate**, the last gate before the first build node, which `state`
marks `gate.slice_gate` true (`chain_revision_approval` on the default chain,
`plan_approval` on one that builds right after its plan gate). `kraft-lite:next`
invokes this skill there while `sliced` is null. Read `KL slices` first: its
`shape` names this chain's `slice_gate`, or says why it is `unsliceable`.
Unsliceable: tell the user its reason and draw nothing. At any other node: tell
the user slicing comes at `shape.slice_gate`, and draw nothing now. In both, go
on with `kraft-lite:next`, so each gate gets `kraft-lite:gate`'s own question.

A plan with no `## Slices` section and few enough tasks for one session's
context is not sliced: say so, delete any `.kraft-lite/proposals/<chain-id>.json`
left by an earlier round, then `KL gate` and invoke `kraft-lite:gate`; the
chain continues as one.

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
5. `KL gate`, then invoke `kraft-lite:gate`. It presents the gate's own
   artifact with the proposal beside it, and records the user's answer: an
   approval as `approve --slices .kraft-lite/proposals/<chain-id>.json`, a
   rejection by deleting the proposal. No slice records exist before the
   approval. A rewind to the slice gate or earlier retires the slices
   (`kraft-lite:gate`): `slices` lists none, and this section runs again.

## 2. Before the first child

Copy this and tick it off; while a line is unticked, no child starts.

```
- [ ] Abilities confirmed from the guide, a local guide, or discover.md, or its fallback named: start, message, be messaged, archive, check in later. Each one with no guide, or that its guide leaves unconfirmed, looked for as discover.md says before its fallback. A missing ability is never a block.
- [ ] One user approval for every branch this run pushes outside this session's own: the feature branch (`feature` mode), and locally the state branch.
- [ ] Setup and the scoped test command run once here, and the working setup line written down.
- [ ] The user has finished giving instructions for this run: ask, and hear "go".
- [ ] Every command taken from a local guide (`.kraft-lite/harnesses/`) listed to the user in that ask.
- [ ] Rights read (section 12), and any the user changed for this run logged with `decide`.
- [ ] `feature` mode: feature branch pushed. State exported (section 7).
```

## 3. Starting a child

A slice's **target** is the branch its PR goes into: the feature branch, or in
`main` mode the base branch. `KL slices` lists what may start:

- `ready`: every slice it comes after is merged. Start it, cut from the
  target's tip (`Cut from: target`).
- `early`: it comes after exactly one slice, and that slice is in review or
  ready to merge. It is cut from that slice's branch
  (`Cut from: <that slice's key>`) and logged in step 3. Start it now when your
  review of that slice has no findings or only small ones; otherwise it waits
  for `ready`.

A slice that comes after two or more slices starts only from `ready`, once all
of them are merged: one branch cannot hold two slices' unmerged work.

For each key you start:

1. `KL slice-set --slice <key> --status starting --branch <slice-branch> --fork <sha>`,
   where `<sha>` is the commit it is cut from (`git rev-parse origin/<branch>`).
   Export state (section 7).
2. Start the session from that commit, where the user said children run for
   this run (locally or in the cloud), else where this session runs. Its own
   branch is the slice branch; its title is `<chain-id> <key>: <slice title>`;
   it is tagged with the chain id where the guide confirms tags; it has
   **an explicit model** (middle tier).
3. `KL slice-set --slice <key> --status running --session <id> --branch <slice-branch>`.
   Started from `early`, also
   `KL decide --slice <key> --question "Start <key> from <slice>'s branch?" --answer "Forked from <branch> at <sha>" --why "..."`.
   Export state.
4. Have a cheapest-tier subagent send the spec and the plan, each in parts
   marked `part i of n` under the message limit. On the same machine, send their
   paths instead.
5. Arm the check-in if none is armed (section 5).

A replacement child for a slice whose branch already holds work (a resume)
continues that branch. Its `Cut from:` line carries the fork `KL slices`
records, from the slice its `cut_from` names, or `target` when that is null.
On the same machine, delete the old child's folder first,
`$(git rev-parse --path-format=absolute --git-common-dir)/kraft-lite/<chain-id>/<key>/`,
so the new child writes its own `fork`, `base` and `chain` files.

The starting instructions, in this order:

1. **Slice:** the controller's chain id, then key, title, its plan tasks, what it
   comes after, which slices run beside it, what it owns or must take from an
   owner's branch, and its line with the `--fork` sha: `Cut from: target at <sha>`
   when cut from the target's tip, `Cut from: <slice key> at <sha>` when cut
   from that slice's branch, where `target` is the word itself, never a branch
   name. Then the state branch: its `kraft-lite-state/chain.json` is what the
   child starts its child chain from.
2. **Controller:** this session's id, and locally its name too (a local session
   message is addressed by name). The child's own tools, copied from your
   guide's "Be messaged" and "Check in later": the one that messages you,
   and the one that schedules a message back into its own session. Where the
   guide marks the child's wake-up unconfirmed, write
   `none; continue on the controller's check-in` in its place. The channels
   in the order to try them: a session message; on the same machine
   `.kraft-lite/messages/<chain-id>/<key>.jsonl` under this checkout's absolute
   path (the controller's chain id; one JSON per line: `at`, `from`, `kind`:
   question|answer|status|done, `body`); else comments on its own draft PR into
   its target, opened at its first commit.
3. **Kraft Lite:** invoke `kraft-lite:build-slice`; if the plugin is missing,
   install it through the harness, else run
   `python3 <marketplace checkout>/plugins/kraft-lite/kl.py` and read the skills
   as files there.
4. **Documents:** the spec and plan arrive next in parts marked `part i of n` (or
   as paths on the same machine); join them, write them down, and confirm.
5. **Setup:** the working setup line from section 2.
6. **Method:** what the child's `state` dispatches for its first build node
   under the slice layer; add no method of your own.
7. **Models:** the policy in section 11, with the model set on every subagent.
8. **Tests:** the scoped test paths for its plan tasks, which its
   `kraft.verify_changed_test_scopes` task runs under the slice layer.
9. **Branches and PRs:** push only its own slice branch; its target, named:
   the feature branch, or in `main` mode the base branch. Its PR goes into the
   target, and so does its `kraft.mr_rebase`. It rebases, and force-pushes its
   own branch, as `kraft-lite:build-slice` says. The repo's rules, quoted from
   its `CLAUDE.md` and `CONTRIBUTING.md`.
10. **Rights:** what it may decide alone, and that every other question comes to
    the controller, never the user; keep working on whatever does not depend on
    a pending answer.
11. **Done message:** PR link, deviations from the plan, what to look at first.

### In `main` mode

There is no feature branch: nothing is pushed for one, and every slice PR goes
to the base branch. Merged means the user merged it: a slice after one ready to
merge is in `early` until then, and a slice after two or more waits for all of
the user's merges. When the user merges a slice, the steps after a merge
(section 6) apply, with the base branch as the target.

## 4. Resuming

Before any other write and before starting anything:

1. Fetch the state branch, `KL import --from <state-checkout>/kraft-lite-state`,
   then `KL chains` for the chain id. An empty `chains` before the import is a
   missing import: never `start` a new chain. An import refused as exported by
   an older Kraft Lite is a run from before V1 chains: tell the user it must be
   finished on the previous Kraft Lite version, and write nothing.
2. `KL slices`. For every `starting` or `running` slice, and every `waiting`
   one in `ready` or `early`, find its session, in the ways the guide
   confirms: the recorded session id; a session whose title or name carries
   `<chain-id>` and `<key>`; the chain-id tag; and its slice branch on the remote
   (`git ls-remote origin <slice-branch>`) as evidence a child ran.
   - Found: `KL slice-set --slice <key> --status running --session <id> --branch <slice-branch>`.
   - Not found, and `starting`, `running`, or its slice branch on the remote:
     never start it blindly. Hold it with
     `KL decide --held --slice <key> --question "No session found for <key>: start a fresh child?" --why "starting a session twice duplicates the work"`,
     tell the user what you found (a pushed branch, an archived session), and
     start one only on their answer.
   - Not found, `waiting` in `ready` or `early`, and no slice branch on the
     remote: start it (section 3).
3. Export state, then run one check-in (section 5): it reports and re-arms.

## 5. Following up

A check-in is armed while any slice is not merged; in `main` mode, until the
nodes `sliced.action` marks `await_user_merges` close. Its message, word for word:
`kraft-lite:run-slices check-in, chain <id>: read state first.` Every check-in:

```
- [ ] `KL state` and `KL slices`. Past the first build node: invoke `kraft-lite:next`, which records the user's merges, and skip to the last line. A non-empty `base_moved`: the rebase in section 6.
- [ ] Read messages, then `.kraft-lite/messages/<chain-id>/`, then each slice PR's comments.
- [ ] Per running child: its status summary (fields in the guide), and `git fetch` + `git log` of its branch. Never its full transcript.
- [ ] A child shown as blocked: find out on what (often its own subagents) before telling the user anything.
- [ ] `main` mode: each `ready_to_merge` slice's PR. One the user merged takes the steps after a merge (section 6).
- [ ] Answer every question (below). Export state.
- [ ] A child that is idle after reporting CI pending, or after a held answer: message it to continue.
- [ ] Report (section 8).
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
  log it with `decide`, or hold it for the user. A binding in
  `.kraft-lite/bindings.yaml` that a child's `start` or `state` refuses is
  configuration: its question opens `For the user: configuration`, and it goes
  to the user.
- A child reports a rebase with a new `Cut from:` line:
  `KL slice-set --slice <key> --fork <sha>`, then export state.

### User-only decisions, and silence

**You never answer a question that belongs to the user, and nothing but the
user's own answer answers it: silence never does.** A push outside the child's own branch, merging
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
| "I'll authorize it so the child can finish" | Your approval does not reach the child's session. It is refused there. |
| "Tell me if you want to approve it" | Wrong door: name the session and the words to type in it. |
| "They said yes to me; I'll relay it and have the child retry" | A relayed yes is refused too. Record it, and send the user to the child's session. The child does not retry until the words appear there. |
| "It's obviously what they'd choose" | Then it costs them one line. Ask. |

Red flags - stop and hold the question: you are about to type "I've authorized",
"the user approved, go ahead", "assuming yes", "proceeding unless", or to run
`answer` without the user's words in front of you.

## 6. Reviewing and merging

Starting a review: `KL slice-set --slice <key> --status in_review`. Your review
is the slice's PR review, on the strongest tier. Read CI at the head you review
and the scoped test paths the PR body lists, and run no tests or checks
yourself. What neither covers goes to the child as a finding. Once your review
has no findings or only small ones, start what `KL slices` lists, as section 3
says.

- Findings to fix: send them to the child, `KL slice-set --slice <key> --status running`,
  and review again when it reports the fix. `KL slices` then lists in
  `base_moved` each slice cut early from it, and each cut early from those:
  once it is back in review, tell each to rebase onto the new tip of the slice
  its `cut_from` names, naming that slice's branch, one after another down the
  line, and record the fork
  each reports (section 5). A recorded fork flags the slices cut from that one.
- `feature` mode: merge into the feature branch only on green CI at the exact
  head you reviewed (pin the head SHA), and only once every slice it comes after
  is merged; a head that moved, read the new commits.
- `main` mode: report the verdict to the user, naming any slice it comes after
  that the user must merge first, then
  `KL slice-set --slice <key> --status ready_to_merge`. The user merges it.
  Start what `KL slices` lists, as section 3 says, and export state.

After a merge (yours, or in `main` mode the user's), in order:

1. `KL slice-set --slice <key> --status merged`. It is refused while a slice it
   comes after is unmerged: record that one first, or leave this one as it is
   until it is merged.
2. Tell each slice cut from this slice's branch, and each slice cut from the
   target, to rebase onto the target. Never one cut from another slice that is
   still unmerged: it rebases when that slice merges. Until then, it rebases
   onto that slice's new tip as above. `cut_from` in `KL slices` says which:
   the parent's key, or null for the target.
3. Archive the child.
4. Start what `KL slices` now lists, as section 3 says.
5. Export state.

A call overturned later reopens the archived child, or starts a fresh one on the
target. When `KL slices` shows `done: true` on every slice (merged; in `main`
mode, ready to merge or merged):

1. Show the whole decisions log, `KL decisions`, while a call is cheap to overturn.
2. `KL decisions --unreported --mark`, to mark it reported.
3. `KL close`, and export state. The rest of the chain then runs per
   `state.sliced.action`, through `kraft-lite:next`, which says where its tasks run.

## 7. State branch

In a cloud session the state branch is this session's own branch, and this
checkout is its checkout. Locally it is the feature branch's name plus `-state`
(in `main` mode, a name for the run plus `-state`), in its own worktree. After every change to slices or decisions, check that
`git -C <state-checkout> branch --show-current` prints the state branch (switch
to it if not; never commit state on another branch), then:

```
python3 "$CLAUDE_PLUGIN_ROOT/kl.py" export --to <state-checkout>/kraft-lite-state --chain-id <id>
git -C <state-checkout> add kraft-lite-state
git -C <state-checkout> diff --cached --quiet || git -C <state-checkout> commit -m "kraft-lite state"
git -C <state-checkout> push origin HEAD:<state-branch>
```

It holds state only (no product code, specs or plans) and is never merged.

## 8. Status

Report by invoking `kraft-lite:status`: it reads `slices` and the decisions log,
marks what it reports, and gives the report its shape.

## 9. Wrap-up

List the branches safe to delete (merged slices; the state branch once done)
for the user to delete. In `feature` mode, the MR node opens the feature PR.

## 10. No guide, or no session tools

With no guide for this harness, or an ability its guide marks unconfirmed,
follow [discover.md](references/discover.md) first. With no way to start a child
even then: say so, and walk the slices here, one after another, in `ready`
order, with the same review, merge and state steps.

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
