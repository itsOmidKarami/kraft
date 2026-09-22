# Getting started

This walks through installing Kraft, connecting a repo, running a work item
end to end, and approving the one gate that stands in its way. Fifteen
minutes, most of it spent waiting on an agent.

!!! tip "Doing this from a coding agent instead"
    Once Kraft is installed (step 1), most of the rest has a `/kraft:*` slash
    command twin — `/kraft:onboard` instead of `repo connect` + `admin init`,
    `/kraft:board` instead of `view list`, `/kraft:gates` instead of `item
    approve`. See [Agent integration](agent-integration.md) if that's closer
    to how you already work.

## 1. Install and start the server

```bash
uv tool install kraft-sdlc
kraft
```

On macOS, `brew tap itsOmidKarami/kraft && brew install kraft` works the same
way — see [Install](install.md) for that path, the install script, and
running from source.

Leave that running — it's the server, in the foreground, on
`http://127.0.0.1:8765`. Open that URL; you should see an empty board.

## 2. Connect a repo

From inside the repo you want Kraft to work on:

```bash
cd ~/code/my-project
kraft repo connect
```

This adds the repo to `repos.yaml` and probes it for a `setup_command` from
the repo's own markers (a `justfile`, a `package.json`, a `pyproject.toml`).
Check what it guessed before trusting it — see
[Configuration → repos.yaml](configuration.md#reposyaml-connected-repos).
A repo with no `setup_command` at all stops its first work item rather than
guessing what to run in a fresh worktree.

## 3. File your first work item

The `quick-task` template has no gates — good for confirming the pipes are
connected before you trust it with something that has an opinion:

```bash
kraft item create "fix the flaky import test" --chain quick-task \
  --description "It's the datetime import, not the fixture."
```

This lands **paused**. Every work item does, whoever or whatever created it —
see [Concepts → Work item](concepts.md#work-item). Nothing has spent a token
yet.

## 4. Start it, and watch

```bash
kraft item resume   # the CLI twin of clicking Start on the board
kraft view watch     # a live board, redrawn on every event
```

![The Kraft board: work items grouped by Needs you, Running, Not started, and Done](assets/board.png)

`quick-task` runs `implementation → verify` with no gate, so if the agent's fix
is good, the item reaches **Done** on its own. `verify` runs the repo's own
`test_scopes` (or `test_command`) from `repos.yaml`, and nothing else: a repo
that declares neither stops at `verify` with a config error naming both keys,
because Kraft will not guess a test command. Declare one, then
`kraft item retry`. If `verify` fails, the item stops
for you with the failing scope named; the `default` chain's `verification` node
is the one that repairs itself within its fix loop's cap
([policy.yaml](configuration.md#policyyaml-caps-budget-archiving)).

## 5. Try the real chain, and its gate

`default` is the chain most work actually runs on — spec, then plan, then
implementation and verification, then two review gates before merge. It's also the default
for `--chain`, so leaving the flag off is enough:

```bash
kraft item create "add a --dry-run flag to the sync command"
```

It stops at the first gate, `spec_approval`, the moment the spec is written:

![Approving a spec_approval gate from the board's side panel](assets/gate.png)

```bash
kraft view docs        # read the spec and plan Kraft wrote
kraft item approve     # or Approve on the board
```

Approving walks it to `plan_approval`, then on into implementation. Reject
instead, with `kraft item reject --note "..."`, and the producing node re-runs
with your note as its steer — see
[Concepts → Gate](concepts.md#gate).

## 6. Review before it merges

```bash
kraft view diff --stat   # how big is it, before you read the whole thing
kraft view diff          # the coloured body, through $PAGER
```

Two gates stand between the work and a merge. `local_review` comes first, with
the work brief the chain wrote: approving it opens a draft merge request, and
Kraft then watches CI and the automated review, repairing what they report.
`chain_review` is the last gate, with the review brief: approving it marks the
merge request ready, waits for its external approval, merges, and watches the
post-merge pipeline — no further input needed unless something goes red. A
rebase that moves the base re-runs `verification` rather than merging over
untested code.

## Where to go from here

- **[Concepts](concepts.md)** — the vocabulary this walkthrough used: chain,
  node, task, gate, cap.
- **[Configuration](configuration.md)** — every field in `library.yaml`,
  `repos.yaml`, `policy.yaml`, `access.yaml`.
- **[Agent integration](agent-integration.md)** — doing all of the above from
  inside a coding-agent session instead of this shell.
- **[Remote access](remote-access.md)** — approving that gate from your phone.
