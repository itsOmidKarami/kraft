# Kraft Lite: concurrent chains

Design for `Kraft-2ej` (two chains in one directory), `Kraft-h4d` (`--chain`
reaches the skills) and `Kraft-f4m` (a chain's hooks are checked against the
registry before it starts).

## Problem

`kl.py` assumes one chain per directory in three places.

`_ours()` (`kl.py:291`) picks the newest chain with unfinished work. A second
`start` therefore captures every later verb, and the first chain's `next`
advances the wrong run with no error. The function says so itself: *"newest-wins,
no way to name an older run."*

`_freeze_chain()` (`kl.py:285`) writes one `.kraft-lite/chain.json`. A second
`start` overwrites it, and if the two runs use different templates the first
chain's `next` dies on `node ... is not in this chain` (`kl.py:344`).

`kl.py start` accepts `--chain` (`kl.py:370`) but `skills/start/SKILL.md` never
mentions it, so `/kraft-lite:start` can only ever run the packaged default.

Two things that look like part of this problem are not:

- **Worktrees already isolate chains.** `repo_root()` walks up for `.git`
  (`kl.py:255`), which exists as a file in a worktree, so each worktree gets its
  own `.kraft-lite/`. Parallel work across worktrees works today.
- **The JSONL backend needs no locking.** `JsonlStore.write` appends
  (`append_jsonl`, `kl.py:159`) and `read_jsonl` merges last-write-per-id. Small
  appends are atomic and two chains write disjoint ids.

## Approach

Name chains explicitly, and refuse to guess when the name is missing and the
answer is ambiguous.

### Chain selection

`--chain-id ID` becomes an optional argument on every stateful verb: `state`,
`close`, `approve`, `gate`, `reject`, `attempt`. Not on `start`, which mints the
id, nor `detect`, which reads no state.

`_ours()` resolves in this order:

| Given | Behaviour |
|---|---|
| explicit `--chain-id` | that chain; exit if this directory holds no such id |
| none, exactly one unfinished | that chain — today's common case, unchanged |
| none, zero unfinished | the newest overall, so a finished chain still reports `done` |
| none, two or more unfinished | exit, listing every unfinished id with its title |

The last row is the fix. The failure it replaces is silent, which is what makes
it worth a breaking change.

### Frozen templates

`_freeze_chain(root, chain, chain_id)` writes
`.kraft-lite/chains/<chain-id>.json`.

`_chain(root, path, chain_id)` resolves: an explicit `--chain` path, then
`chains/<chain-id>.json`, then the legacy `.kraft-lite/chain.json`, then the
packaged default. The legacy rung keeps chains already in flight walking across
the upgrade; it is removed at the next breaking release.

Per-chain templates are also what let `--chain` mean anything: today a template
given at `start` is a property of the directory, and the next `start` silently
retargets it.

### Registry validation

`start` collects every hook named in `nodes[].tasks` and checks each one appears
in `.kraft-lite/registry.yaml`, exiting with the unbound names if any are
missing.

A *missing* registry warns on stderr rather than exiting. The `start` skill
already refuses to run without one, so an engine-level error would duplicate a
guard that exists a layer up — at the cost of a fixture in all sixteen tests
that call `start`. The warning keeps a non-skill caller informed without that
churn.

`kl.py` imports nothing outside the standard library and
`test_the_shipped_helper_imports_nothing_beyond_the_stdlib` enforces that, so
this does not parse YAML. Registry hooks are unquoted top-level keys, so a
`^([a-z0-9_.]+):` scan finds them. It proves a hook is bound, not that its
binding is well-formed, which is the whole of what the node needs to run.

### Skills

`start` documents `--chain <path>`, reports the id it minted, and says every
later verb in this chain carries `--chain-id <id>`. `next`, `gate` and `status`
pass that id when they know it, and on the ambiguity error show the human the
list rather than picking.

A chain id survives a cleared context through the skill prose and, failing that,
through the error that lists the candidates. No pointer file: a "current chain"
marker is a mutable global that two chains would flip between, and the human is
sitting in front of an attended tool.

## Testing

`test_a_second_start_becomes_the_live_chain` survives unchanged. Its first chain
is walked to `done` before the second starts, so it never has two *unfinished*
chains and the new ambiguity error cannot fire. The behaviour it pins — a
finished chain yielding to a new one — is behaviour this design keeps.

`test_a_node_missing_from_the_chain_fails_rather_than_reporting_no_hooks`
(`tests/test_backend_bd.py:281`) does change: it plants a broken template at the
legacy `.kraft-lite/chain.json`, which the new resolution order reaches only
after `chains/<chain-id>.json`. It plants the file under the chain's own id
instead.

New coverage: an unknown `--chain-id` exits; a legacy `chain.json` still
resolves; validation rejects an unbound hook and accepts the shipped default
against a registry that binds it. `test_skills.py` gains an assertion that every
verb-invoking skill mentions `--chain-id`.

## Out of scope

JSONL locking, for the reason given above. `Kraft-1x2` — `detect` cannot offer
bindings for hooks a custom chain invents, because `HOOK_KEYWORDS` (`kl.py:441`)
is a fixed dict of the ten default hooks — is a separate bead about `detect`, not
about concurrency.
