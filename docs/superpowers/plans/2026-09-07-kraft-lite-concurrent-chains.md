# Kraft Lite Concurrent Chains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one directory hold several Kraft Lite chains, each named explicitly, so no verb ever silently advances the wrong run.

**Architecture:** `_ours()` gains an optional chain id and refuses to guess when two or more chains are unfinished. Frozen chain templates move to one file per chain id, with the old single-file path kept as a fallback rung so runs already in flight survive the upgrade. `start` checks the chain's hooks against the registry before materializing anything. The five skills learn to carry the id.

**Tech Stack:** Python 3.10+, standard library only. pytest for tests.

**Spec:** `docs/superpowers/specs/2026-09-07-kraft-lite-concurrent-chains-design.md`

## Global Constraints

- `plugins/kraft-lite/kl.py` imports nothing outside the standard library. `tests/test_records.py::test_the_shipped_helper_imports_nothing_beyond_the_stdlib` enforces this. No PyYAML, no third-party anything.
- Python 3.10 is the floor. No `match` statement syntax newer than 3.10, no `itertools.batched`.
- Nothing in the plugin may reference a path outside `plugins/kraft-lite/`. `tests/test_walk_end_to_end.py::test_nothing_in_the_plugin_reaches_outside_the_plugin` enforces this.
- Run tests with `pytest plugins/kraft-lite/tests -q` from the repo root. Do NOT run `just test` — that is the Kraft backend suite, roughly 14 minutes, and it does not cover this plugin.
- The state directory constant is `kl.STATE_DIR` (`".kraft-lite"`). Never hardcode the string in new code.
- Existing tests call `kl.main(["start", ...])` in a bare `tmp_path` with no registry file. No task may make that fail.

---

### Task 1: `_ours()` takes a chain id and refuses to guess

**Files:**
- Modify: `plugins/kraft-lite/kl.py:291-317` (`_ours`)
- Test: `plugins/kraft-lite/tests/test_backend_bd.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_ours(records: list[dict], chain_id: str | None = None) -> list[dict]`, and `chain_index(records: list[dict]) -> dict[str, list[dict]]` returning every chain in the store keyed by epic id, insertion-ordered oldest first.

- [ ] **Step 1: Write the failing tests**

Add to `plugins/kraft-lite/tests/test_backend_bd.py`:

```python
def _two_unfinished(tmp_path, monkeypatch, capsys):
    """Two chains started in one directory, neither walked to done."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "first"])
    first = json.loads(capsys.readouterr().out)["chain_id"]
    kl.main(["start", "--title", "second"])
    second = json.loads(capsys.readouterr().out)["chain_id"]
    return first, second


def test_two_unfinished_chains_and_no_id_refuses_to_guess(tmp_path, monkeypatch, capsys):
    """The old behaviour walked the newest silently, so the first chain's next
    advanced the wrong run with no error at all."""
    first, second = _two_unfinished(tmp_path, monkeypatch, capsys)
    with pytest.raises(SystemExit) as caught:
        kl.main(["state"])
    message = str(caught.value)
    assert first in message and second in message, "both ids must be offered"
    assert "first" in message and "second" in message, "titles, so the human can tell them apart"
    assert "--chain-id" in message, "and the way out"


def test_an_explicit_chain_id_selects_the_older_chain(tmp_path, monkeypatch, capsys):
    first, _second = _two_unfinished(tmp_path, monkeypatch, capsys)
    kl.main(["state", "--chain-id", first])
    state = json.loads(capsys.readouterr().out)
    assert state["chain_id"] == first
    assert state["node"] == "spec"


def test_an_unknown_chain_id_names_the_chains_that_are_here(tmp_path, monkeypatch, capsys):
    first, second = _two_unfinished(tmp_path, monkeypatch, capsys)
    with pytest.raises(SystemExit) as caught:
        kl.main(["state", "--chain-id", "kl-nosuch"])
    message = str(caught.value)
    assert "kl-nosuch" in message
    assert first in message and second in message


def test_one_unfinished_chain_still_needs_no_id(tmp_path, monkeypatch, capsys):
    """The common case must not regress into demanding a flag."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "only"])
    capsys.readouterr()
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["node"] == "spec"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest plugins/kraft-lite/tests/test_backend_bd.py -q -k "unfinished or chain_id"`

Expected: FAIL. The first three error on `unrecognized arguments: --chain-id` or on no exception being raised; `test_one_unfinished_chain_still_needs_no_id` passes already.

Note the `--chain-id` argument does not exist yet — that is Task 2. These tests fail for the right reason once Task 1's `_ours` is in, and go green at the end of Task 2. Implement Task 1's function now, and expect `test_two_unfinished_chains_and_no_id_refuses_to_guess` and `test_one_unfinished_chain_still_needs_no_id` to pass at the end of this task while the two `--chain-id` tests stay red until Task 2.

- [ ] **Step 3: Replace `_ours` in `plugins/kraft-lite/kl.py`**

```python
def chain_index(records: list[dict]) -> dict[str, list[dict]]:
    """Every chain in this store, keyed by its epic id, oldest first. Node ids are
    `<chain-id>.<node-id>`, so the prefix names the owner."""
    epics = [r["id"] for r in records if label_value(r, CHAIN_LABEL)]
    by_chain: dict[str, list[dict]] = {e: [] for e in epics}
    for record in records:
        owner = record["id"].split(".")[0]
        if owner in by_chain:
            by_chain[owner].append(record)
    return by_chain


def _titled(by_chain: dict[str, list[dict]], ids: list[str]) -> str:
    lines = []
    for chain_id in ids:
        epic = next((r for r in by_chain[chain_id] if label_value(r, CHAIN_LABEL)), None)
        lines.append(f"  {chain_id}  {epic.get('title', '') if epic else ''}")
    return "\n".join(lines)


def _ours(records: list[dict], chain_id: str | None = None) -> list[dict]:
    """The chain a verb acts on.

    A directory accumulates one epic per `start`. Picking the newest unfinished
    one silently is how the wrong run gets advanced, so two or more unfinished
    chains and no id is an error rather than a guess. With one unfinished chain
    the id stays optional, which is the case nearly every run is in.
    """
    by_chain = chain_index(records)
    if not by_chain:
        return []
    if chain_id is not None:
        if chain_id not in by_chain:
            raise SystemExit(
                f"kraft-lite: no chain {chain_id!r} in this directory. Chains here:\n"
                + _titled(by_chain, list(by_chain))
            )
        return by_chain[chain_id]

    def unfinished(candidate: str) -> bool:
        return any(r["status"] != "closed" for r in by_chain[candidate])

    live = [e for e in by_chain if unfinished(e)]
    if len(live) > 1:
        raise SystemExit(
            "kraft-lite: this directory has more than one unfinished chain. "
            "Pass --chain-id to say which:\n" + _titled(by_chain, live)
        )
    # No unfinished chain means the newest overall, so a completed run still
    # reports `done` rather than `unstarted`.
    return by_chain[(live or list(by_chain))[-1]]
```

- [ ] **Step 4: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: everything passes except `test_an_explicit_chain_id_selects_the_older_chain` and `test_an_unknown_chain_id_names_the_chains_that_are_here`, which still fail on `unrecognized arguments: --chain-id`. In particular `test_a_second_start_becomes_the_live_chain` must still pass — its first chain is walked to `done` before the second starts, so it never has two unfinished chains.

- [ ] **Step 5: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_backend_bd.py
git commit -m "kraft-lite: refuse to guess between two unfinished chains"
```

---

### Task 2: `--chain-id` on every stateful verb

**Files:**
- Modify: `plugins/kraft-lite/kl.py:320-361` (`_state`), `kl.py:364-434` (`main`)
- Test: `plugins/kraft-lite/tests/test_backend_bd.py` (tests from Task 1 go green)

**Interfaces:**
- Consumes: `_ours(records, chain_id)` and `chain_index(records)` from Task 1.
- Produces: `_state(root: Path, chain: dict, chain_id: str | None) -> dict`; every stateful verb accepts `--chain-id ID`.

- [ ] **Step 1: Write the failing test**

Add to `plugins/kraft-lite/tests/test_backend_bd.py`:

```python
def test_every_stateful_verb_accepts_a_chain_id(tmp_path, monkeypatch, capsys):
    """A gate answered on the wrong chain is the failure this whole change is
    about, so the id has to reach the writing verbs, not only `state`."""
    first, second = _two_unfinished(tmp_path, monkeypatch, capsys)
    kl.main(["gate", "--name", "spec_approval", "--chain-id", first])
    capsys.readouterr()
    kl.main(["state", "--chain-id", first])
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    kl.main(["state", "--chain-id", second])
    assert json.loads(capsys.readouterr().out)["status"] == "open", "the other chain is untouched"

    kl.main(["approve", "--chain-id", first])
    capsys.readouterr()
    kl.main(["state", "--chain-id", first])
    assert json.loads(capsys.readouterr().out)["node"] == "plan"

    kl.main(["reject", "--note", "not yet", "--chain-id", first])
    capsys.readouterr()
    kl.main(["state", "--chain-id", first])
    assert "not yet" in json.loads(capsys.readouterr().out)["note"]

    kl.main(["attempt", "--chain-id", second])
    assert json.loads(capsys.readouterr().out)["chain_id"] == second

    kl.main(["close", "--chain-id", second])
    capsys.readouterr()
    kl.main(["state", "--chain-id", second])
    assert json.loads(capsys.readouterr().out)["node"] == "plan"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest plugins/kraft-lite/tests/test_backend_bd.py::test_every_stateful_verb_accepts_a_chain_id -q`

Expected: FAIL with `error: unrecognized arguments: --chain-id`.

- [ ] **Step 3: Thread the id through `_state` and `main`**

In `kl.py`, change `_state`'s signature and its first line:

```python
def _state(root: Path, chain: dict, chain_id: str | None = None) -> dict:
    records = _ours(store_for(root).load(), chain_id)
```

The rest of `_state` is unchanged.

In `main`, register the argument on every stateful verb and pass it down. Replace the parser block and the dispatch block:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kl")
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--chain", type=Path, default=None)
    sub.add_parser("detect")

    gate = sub.add_parser("gate")
    gate.add_argument("--name", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--note", required=True)
    for name in ("state", "close", "approve", "attempt"):
        sub.add_parser(name)
    # Every verb that reads or writes chain state can be told which chain. `start`
    # mints its own id and `detect` touches no state, so neither takes one.
    for name in ("state", "close", "approve", "attempt", "gate", "reject"):
        sub.choices[name].add_argument("--chain-id", default=None)

    args = parser.parse_args(argv)
    root = repo_root()

    if args.verb == "detect":
        print(json.dumps(detect(root), indent=2))
        return 0

    chain_id = getattr(args, "chain_id", None)
    chain = _chain(root, getattr(args, "chain", None))
    store = store_for(root)

    if args.verb == "start":
        new_id = new_chain_id()
        _freeze_chain(root, chain)
        records = materialize(chain, args.title, new_id)
        # The epic is a container, not a step: close it now so the walk starts at
        # the first real node.
        records[0]["status"] = "closed"
        store.write(records)
        print(json.dumps({"chain_id": new_id, "backend": backend(root)}))
        return 0

    node_record = current(_ours(store.load(), chain_id))
    if node_record is None and args.verb != "state":
        raise SystemExit("kraft-lite: no open node — the chain is finished or was never started")

    if args.verb == "state":
        print(json.dumps(_state(root, chain, chain_id), indent=2))
        return 0

    if args.verb in ("close", "approve"):
        store.write([dict(node_record, status="closed")])
    elif args.verb == "gate":
        store.write([set_label(dict(node_record, status="blocked"), GATE_LABEL, args.name)])
    elif args.verb == "reject":
        description = node_record.get("description", "")
        store.write(
            [
                dict(
                    node_record,
                    status="open",
                    description=f"{description}\n{NOTE_PREFIX}{args.note}",
                )
            ]
        )
    elif args.verb == "attempt":
        count = attempts(node_record) + 1
        store.write([set_label(node_record, ATTEMPT_LABEL, str(count))])
        state = _state(root, chain, chain_id)
        state["over_cap"] = state["cap"] is not None and count > state["cap"]
        print(json.dumps(state, indent=2))
        return 0

    print(json.dumps(_state(root, chain, chain_id), indent=2))
    return 0
```

Note `_freeze_chain(root, chain)` and `_chain(root, path)` keep their current single-file signatures here. Task 3 changes both.

- [ ] **Step 4: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: all pass, including the four tests from Task 1.

- [ ] **Step 5: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_backend_bd.py
git commit -m "kraft-lite: --chain-id on every stateful verb"
```

---

### Task 3: One frozen template per chain

**Files:**
- Modify: `plugins/kraft-lite/kl.py:266-288` (`_chain`, `_freeze_chain`), and their two call sites in `main`
- Modify: `plugins/kraft-lite/tests/test_backend_bd.py:281-296` (`test_a_node_missing_from_the_chain_fails_rather_than_reporting_no_hooks`)
- Test: `plugins/kraft-lite/tests/test_backend_bd.py`

**Interfaces:**
- Consumes: `_ours` / `chain_index` from Task 1, `chain_id` resolution in `main` from Task 2.
- Produces: `_chain(root: Path, path: Path | None, chain_id: str | None = None) -> dict` and `_freeze_chain(root: Path, chain: dict, chain_id: str) -> None`. Frozen templates live at `.kraft-lite/chains/<chain-id>.json`.

- [ ] **Step 1: Write the failing tests**

Add to `plugins/kraft-lite/tests/test_backend_bd.py`:

```python
def test_two_chains_keep_their_own_templates(tmp_path, monkeypatch, capsys):
    """One frozen chain.json meant the second start overwrote the first chain's
    template, and the first chain's next then died on 'not in this chain'."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "quick.json"
    custom.write_text(
        json.dumps(
            {
                "id": "quick-task",
                "nodes": [
                    {
                        "id": "verify",
                        "tasks": ["on.test.run"],
                        "gate_after": None,
                        "fix_loop": "verify_fix_loop",
                    }
                ],
                "loops": {"verify_fix_loop": {"attempts": 2}},
            }
        )
    )
    kl.main(["start", "--title", "custom", "--chain", str(custom)])
    custom_id = json.loads(capsys.readouterr().out)["chain_id"]
    kl.main(["start", "--title", "stock"])
    stock_id = json.loads(capsys.readouterr().out)["chain_id"]

    kl.main(["state", "--chain-id", custom_id])
    assert json.loads(capsys.readouterr().out)["node"] == "verify"
    kl.main(["state", "--chain-id", stock_id])
    assert json.loads(capsys.readouterr().out)["node"] == "spec"


def test_a_legacy_frozen_chain_still_resolves(tmp_path, monkeypatch, capsys):
    """Chains started before per-chain templates must keep walking across the
    upgrade rather than dying on a missing file."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]

    # Simulate the old layout: move the per-chain file back to the single path.
    per_chain = tmp_path / kl.STATE_DIR / "chains" / f"{chain_id}.json"
    legacy = tmp_path / kl.STATE_DIR / "chain.json"
    legacy.write_text(per_chain.read_text())
    per_chain.unlink()

    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["node"] == "spec"
```

Change the existing `test_a_node_missing_from_the_chain_fails_rather_than_reporting_no_hooks` to plant its broken template under the chain's own id, because the legacy path is now only reached as a fallback:

```python
def test_a_node_missing_from_the_chain_fails_rather_than_reporting_no_hooks(
    tmp_path, monkeypatch, capsys
):
    """`hooks: []` tells the next skill to close and move on, so a broken run
    walks to done having executed nothing."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]
    (tmp_path / kl.STATE_DIR / "chains" / f"{chain_id}.json").write_text(
        json.dumps({"id": "other", "nodes": [], "loops": {}})
    )
    with pytest.raises(SystemExit) as caught:
        kl.main(["state"])
    assert "not in this chain" in str(caught.value)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest plugins/kraft-lite/tests/test_backend_bd.py -q -k "own_templates or legacy_frozen or missing_from_the_chain"`

Expected: `test_two_chains_keep_their_own_templates` FAILS (the second start overwrites the template, so the custom chain reports `spec`); `test_a_legacy_frozen_chain_still_resolves` FAILS on `FileNotFoundError` for the `chains/` path; the rewritten missing-node test FAILS on `FileNotFoundError` for the same reason.

- [ ] **Step 3: Make templates per-chain**

Replace `_chain` and `_freeze_chain` in `kl.py`:

```python
DEFAULT_CHAIN = Path(__file__).parent / "chains" / "default.json"


def _frozen_path(root: Path, chain_id: str) -> Path:
    return root / STATE_DIR / "chains" / f"{chain_id}.json"


def _chain(root: Path, path: Path | None, chain_id: str | None = None) -> dict:
    """The chain this run walks.

    `start` freezes its chain and every later verb reads that copy. Re-reading the
    template each time would mean a `--chain` given at start is forgotten by the
    next verb — and worse, that editing a template retargets a run already in
    flight. One file per chain id is what lets two runs in one directory hold
    different templates.
    """
    if path is not None:
        return json.loads(path.read_text())
    if chain_id is not None:
        per_chain = _frozen_path(root, chain_id)
        if per_chain.is_file():
            return json.loads(per_chain.read_text())
    # Chains started before per-chain templates. Remove at the next breaking release.
    legacy = root / STATE_DIR / "chain.json"
    if legacy.is_file():
        return json.loads(legacy.read_text())
    return json.loads(DEFAULT_CHAIN.read_text())


def _freeze_chain(root: Path, chain: dict, chain_id: str) -> None:
    frozen = _frozen_path(root, chain_id)
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(json.dumps(chain, indent=2) + "\n")
```

In `main`, the chain can only be loaded once the chain id is known, so move the `_chain` call below the id resolution. Replace the block between `root = repo_root()` and the `start` branch:

```python
    chain_id = getattr(args, "chain_id", None)
    store = store_for(root)

    if args.verb == "start":
        new_id = new_chain_id()
        chain = _chain(root, args.chain)
        _freeze_chain(root, chain, new_id)
        records = materialize(chain, args.title, new_id)
        # The epic is a container, not a step: close it now so the walk starts at
        # the first real node.
        records[0]["status"] = "closed"
        store.write(records)
        print(json.dumps({"chain_id": new_id, "backend": backend(root)}))
        return 0

    records = _ours(store.load(), chain_id)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    resolved_id = epic["id"] if epic else None
    chain = _chain(root, None, resolved_id)
    node_record = current(records)
```

Then pass `resolved_id` rather than `chain_id` into every `_state(root, chain, ...)` call in the rest of `main`, so a verb run without the flag still reads the template of the chain it actually resolved.

Update `_state` to match — it re-resolves from the store, and must use the same id:

```python
def _state(root: Path, chain: dict, chain_id: str | None = None) -> dict:
    records = _ours(store_for(root).load(), chain_id)
```

- [ ] **Step 4: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: all pass. `test_a_custom_chain_survives_the_verbs_that_follow_start` must still pass — it has one chain, which now resolves by id instead of by the single path.

- [ ] **Step 5: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_backend_bd.py
git commit -m "kraft-lite: freeze one chain template per chain id"
```

---

### Task 4: `start` checks the chain's hooks against the registry

**Files:**
- Modify: `plugins/kraft-lite/kl.py` (add `registry_hooks` and `validate_hooks` near `detect`; call from `main`'s `start` branch)
- Test: `plugins/kraft-lite/tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `registry_hooks(root: Path) -> set[str] | None` (None when there is no registry file) and `validate_hooks(root: Path, chain: dict) -> None` (raises `SystemExit` listing unbound hooks).

- [ ] **Step 1: Write the failing tests**

Add to `plugins/kraft-lite/tests/test_detect.py`:

```python
def _registry(root, hooks):
    directory = root / kl.STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    body = "# a registry\n" + "".join(
        f"{hook}:\n  kind: prompt\n  prompt: do the thing\n  # also found: (none)\n"
        for hook in hooks
    )
    (directory / "registry.yaml").write_text(body)


def test_registry_hooks_reads_the_top_level_keys_only(tmp_path):
    """kl.py may not import yaml, so the scan has to be a regex. Nested keys are
    indented and must not be mistaken for hooks."""
    _registry(tmp_path, ["on.spec.requested", "on.test.run"])
    assert kl.registry_hooks(tmp_path) == {"on.spec.requested", "on.test.run"}


def test_registry_hooks_is_none_when_there_is_no_registry(tmp_path):
    assert kl.registry_hooks(tmp_path) is None


def test_start_rejects_a_chain_whose_hooks_are_not_bound(tmp_path, monkeypatch, capsys):
    """An unbound hook is otherwise only discovered mid-walk, by which point the
    chain has already run its earlier nodes."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    _registry(tmp_path, ["on.spec.requested"])
    with pytest.raises(SystemExit) as caught:
        kl.main(["start", "--title", "t"])
    message = str(caught.value)
    assert "on.plan.requested" in message, "the unbound hooks are named"
    assert "on.spec.requested" not in message, "the bound one is not"


def test_start_accepts_the_shipped_chain_against_a_full_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    _registry(tmp_path, sorted({t for n in chain["nodes"] for t in n["tasks"]}))
    kl.main(["start", "--title", "t"])
    assert "chain_id" in json.loads(capsys.readouterr().out)


def test_start_without_a_registry_warns_but_runs(tmp_path, monkeypatch, capsys):
    """The start skill already refuses to run without a registry. Erroring here
    too would only cost every existing test a fixture."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    captured = capsys.readouterr()
    assert "chain_id" in json.loads(captured.out)
    assert "registry" in captured.err
```

`test_detect.py` needs `import json`, `import pytest`, and a `PLUGIN` constant if it lacks them; check its header and add what is missing.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest plugins/kraft-lite/tests/test_detect.py -q -k "registry"`

Expected: FAIL with `AttributeError: module 'kl' has no attribute 'registry_hooks'`, and the two `start` tests failing because no exception or warning is produced.

- [ ] **Step 3: Add the check**

Add to `kl.py`, above `main` (and add `import re` to the imports if absent):

```python
#: A registry hook is an unquoted key at column zero. Everything under it — `kind`,
#: `skill`, `prompt` — is indented, and comments start with `#`. That is enough
#: structure to find the hooks with a scan, which is what keeps kl.py free of a
#: YAML dependency it would otherwise need for this one check.
HOOK_KEY = re.compile(r"^([a-z][a-z0-9_.]*):", re.MULTILINE)


def registry_hooks(root: Path) -> set[str] | None:
    """The hooks bound in this repo's registry, or None when there is no registry."""
    path = root / STATE_DIR / "registry.yaml"
    if not path.is_file():
        return None
    return set(HOOK_KEY.findall(path.read_text()))


def validate_hooks(root: Path, chain: dict) -> None:
    """A hook with no binding is otherwise found mid-walk, after earlier nodes have
    already run."""
    bound = registry_hooks(root)
    if bound is None:
        print(
            f"kraft-lite: no {STATE_DIR}/registry.yaml — starting without checking "
            "the chain's hooks. Run the init skill to write one.",
            file=sys.stderr,
        )
        return
    wanted = {task for node in chain["nodes"] for task in node["tasks"]}
    missing = sorted(wanted - bound)
    if missing:
        raise SystemExit(
            "kraft-lite: this chain names hooks with no registry binding: "
            + ", ".join(missing)
            + f"\nAdd them to {STATE_DIR}/registry.yaml, or re-run the init skill."
        )
```

Call it in `main`'s `start` branch, immediately after the chain loads and before anything is written:

```python
    if args.verb == "start":
        new_id = new_chain_id()
        chain = _chain(root, args.chain)
        validate_hooks(root, chain)
        _freeze_chain(root, chain, new_id)
```

- [ ] **Step 4: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: all pass. Existing tests that call `start` in a bare `tmp_path` now emit a stderr warning and otherwise behave as before.

- [ ] **Step 5: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_detect.py
git commit -m "kraft-lite: check a chain's hooks against the registry at start"
```

---

### Task 5: The skills carry the chain id

**Files:**
- Modify: `plugins/kraft-lite/skills/start/SKILL.md`, `skills/next/SKILL.md`, `skills/gate/SKILL.md`, `skills/status/SKILL.md`
- Test: `plugins/kraft-lite/tests/test_skills.py`

**Interfaces:**
- Consumes: the `--chain-id` and `--chain` arguments from Tasks 2 and 3.
- Produces: no code interface. Prose that makes an agent pass the id.

- [ ] **Step 1: Write the failing tests**

Add to `plugins/kraft-lite/tests/test_skills.py`:

```python
def test_every_stateful_skill_passes_the_chain_id():
    """A cleared context has only the skill prose and the disk. If the skills do
    not carry the id, two chains in one directory stall on the ambiguity error."""
    for name in ("next", "gate", "status"):
        body = (SKILLS_DIR / name / "SKILL.md").read_text()
        assert "--chain-id" in body, f"the {name} skill must pass the chain id"


def test_the_start_skill_documents_the_chain_argument():
    """kl.py has accepted --chain since the first release; the skill never said
    so, which is why /kraft-lite:start could only ever run the default chain."""
    body = (SKILLS_DIR / "start" / "SKILL.md").read_text()
    assert "--chain " in body or "--chain <" in body
    assert "--chain-id" in body, "and it must hand the minted id onward"
```

`SKILLS_DIR` may be named differently in this file — check the existing constants at the top of `test_skills.py` and reuse whatever it already uses to reach a skill body.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest plugins/kraft-lite/tests/test_skills.py -q -k "chain"`

Expected: FAIL on the assertion that `--chain-id` appears in `next/SKILL.md`.

- [ ] **Step 3: Update the four skill bodies**

In `skills/start/SKILL.md`, replace the command block and the paragraph under it with:

````markdown
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

To run a chain other than the packaged default, add `--chain <path-to-chain.json>`.
The template is frozen against this run, so a later edit to that file does not
retarget a chain already in flight.

It prints the chain id and which backend holds the state (`bd`, or a JSONL file
under `.kraft-lite/`). Say both, so the human knows where their state lives and
which chain is theirs.

Carry that id. Every later verb in this chain takes `--chain-id <id>`, and a
directory holding two unfinished chains will refuse to guess between them.
````

In `skills/next/SKILL.md`, change the state command and add a paragraph after it:

````markdown
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

Every command in this skill takes `--chain-id`. Omit it only when you know this
directory holds one chain. If a verb exits saying there is more than one
unfinished chain, show the human the list it printed and ask which — do not pick.
````

Apply the same `--chain-id <id>` suffix to the `gate`, `close`, and `attempt` commands further down that file.

In `skills/gate/SKILL.md`, suffix the three commands:

````markdown
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>" --chain-id <id>
````

In `skills/status/SKILL.md`, suffix its `state` command the same way and add:

````markdown
With more than one chain in the directory, report each one — run `state` per id.
A status that silently covers one of two chains is worse than no status.
````

- [ ] **Step 4: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: all pass. `test_no_skill_cites_a_hook_the_chain_does_not_have`, `test_every_skill_invokes_the_helper_by_plugin_root`, and `test_every_skill_can_find_the_helper_without_the_plugin_variable` all read these files and must stay green.

- [ ] **Step 5: Commit**

```bash
git add plugins/kraft-lite/skills plugins/kraft-lite/tests/test_skills.py
git commit -m "kraft-lite: skills carry the chain id and document --chain"
```

---

### Task 6: README and the beads

**Files:**
- Modify: `plugins/kraft-lite/README.md`
- Test: `plugins/kraft-lite/tests/test_skills.py::test_the_readme_describes_the_chain_it_actually_ships` (existing, must stay green)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Update the README**

Under `## State`, add:

````markdown
A directory can hold several chains. Each verb takes `--chain-id <id>`; with one
unfinished chain the flag is optional, and with two or more it is required — Lite
refuses to guess which run a gate belongs to. Frozen chain templates live in
`.kraft-lite/chains/<chain-id>.json`.
````

In `## What it does not do`, remove `parallel chains` from the list, since one directory now runs several. Leave the rest of that sentence intact.

- [ ] **Step 2: Run the tests**

Run: `pytest plugins/kraft-lite/tests -q`

Expected: all pass, including `test_the_readme_describes_the_chain_it_actually_ships` and `test_the_readme_cites_no_path_outside_the_plugin`.

- [ ] **Step 3: Close the beads**

```bash
bd close Kraft-2ej Kraft-h4d Kraft-f4m
```

- [ ] **Step 4: Commit**

```bash
git add plugins/kraft-lite/README.md
git commit -m "kraft-lite: document concurrent chains"
```
