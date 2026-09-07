# Kraft Lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `kraft-lite` plugin that runs a Kraft chain inside one agent session — node ordering, gates and loop caps — with no orchestrator process.

**Architecture:** A plugin directory holds five namespaced skills plus one stdlib-only Python helper, `kl.py`. The agent reads the chain and registry YAML itself (it is an LLM; it does not need a parser) and calls `kl.py` only for the parts that must be deterministic and tested: state records and environment detection. State lives in `bd` when `bd` is present, otherwise in a JSONL file written in `bd export` format so `bd import` adopts it later.

**Tech Stack:** Python 3.14 stdlib only for the shipped helper (`json`, `pathlib`, `shutil`, `subprocess`, `argparse`). `pytest` for tests, dev-time only. No new runtime dependencies — the plugin must run in a repo that has never heard of the `kraft` package.

**Spec:** `docs/superpowers/specs/2026-09-07-kraft-lite-design.md`

**Status:** executed. Seven defects were found during execution and after review;
all are fixed on the branch and the corrections are folded into the tasks below.
Four of them shared one cause — the bd backend was written against a faked
`subprocess.run` and never against the binary, so the dependency shape, the
`export -o -` flag, the walk order and the `updated_at` upsert guard were all
wrong together and all green. `tests/test_backend_bd_real.py` is the answer to
that class of defect: when `bd` is on PATH it drives a throwaway database, and it
skips otherwise.

## Global Constraints

- The shipped plugin MUST NOT import `kraft` or any third-party package. `kl.py` is stdlib-only. A test enforces this.
- Plugin name is `kraft-lite`. Skills load as `/kraft-lite:init`, `:start`, `:next`, `:gate`, `:status`.
- Chain and policy definitions have exactly one source: `templates/default.yaml` and `templates/policy.yaml` in this repo. The plugin ships a generated JSON copy; a test fails if the copy drifts.
- Record format is `bd export`'s: `{"_type": "issue", "id", "title", "status", "description", "labels", "dependencies"}`. `status` is one of `open`, `blocked`, `closed`.
- Registry handler kinds in Lite: `skill`, `prompt`, `subprocess`. `agent` and `builtin` are refused by name.
- Ruff: line-length 100, rules `E,F,W,I,UP,B`. Run `just lint` before each commit.
- Run targeted tests, not the full suite: `uv run pytest plugins/kraft-lite/tests -q`.

---

## File Structure

| Path | Responsibility |
|---|---|
**Everything under `plugins/kraft-lite/` is published as its own public repo** by
`git subtree split` (Task 6). So that directory must be a working standalone
checkout: its own tests, its own CI, no import of anything above it. Whatever
needs this monorepo lives outside it.

Published — the future `kraft-lite` repo:

| Path | Responsibility |
|---|---|
| `plugins/kraft-lite/.claude-plugin/plugin.json` | manifest; the reason skills are namespaced |
| `plugins/kraft-lite/kl.py` | the only shipped code: records, state machine, detection |
| `plugins/kraft-lite/chains/default.json` | the chain, generated (never hand-edited) |
| `plugins/kraft-lite/skills/{init,start,next,gate,status}/SKILL.md` | the spine, as prose |
| `plugins/kraft-lite/tests/test_records.py` | materialize / current / attempts, JSONL backend |
| `plugins/kraft-lite/tests/test_backend_bd.py` | the `bd` backend and backend selection |
| `plugins/kraft-lite/tests/test_detect.py` | detection branches |
| `plugins/kraft-lite/tests/test_skills.py` | skill prose checked against the real chain |
| `plugins/kraft-lite/tests/test_walk_end_to_end.py` | one whole chain through the CLI |
| `plugins/kraft-lite/.github/workflows/test.yml` | proves the standalone checkout passes on its own |
| `plugins/kraft-lite/LICENSE` | nothing is open-source without one |
| `plugins/kraft-lite/README.md` | install, state, upgrade path, non-goals |

Not published — this monorepo's job:

| Path | Responsibility |
|---|---|
| `dev/build_lite_chain.py` | renders `templates/*.yaml` to the shipped JSON; needs PyYAML, so it cannot live in the plugin |
| `tests/kraft_lite_artifact_test.py` | the drift guard — the one test that must see both the YAML and the JSON |
| `justfile` | `lite-build` (regenerate) and `lite-publish` (subtree split and push) |

---

### Task 1: The generated chain artifact

The plugin cannot parse YAML (no PyYAML in a stdlib-only helper), and it must not
hand-copy the node list. So the YAML stays the single source and a `just` recipe
renders it to JSON. A test regenerates and compares, which fails the moment
someone edits the YAML without rebuilding.

**Files:**
- Create: `dev/build_lite_chain.py`
- Create: `plugins/kraft-lite/chains/default.json` (generated in Step 3)
- Modify: `justfile` (add recipe near the other build recipes)
- Modify: `pyproject.toml` (`testpaths`)
- Test: `tests/kraft_lite_artifact_test.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `dev.build_lite_chain.render(chain_yaml: Path, policy_yaml: Path) -> dict` returning
  `{"id": str, "nodes": [{"id": str, "tasks": [str], "gate_after": str | None, "fix_loop": str | None}], "loops": {str: {"attempts": int}}}`.
  The artifact at `plugins/kraft-lite/chains/default.json` is that dict, `json.dumps(..., indent=2)` plus a trailing newline.

- [ ] **Step 1: Write the failing test**

Create `tests/kraft_lite_artifact_test.py`. This is the only Lite test that lives
in this repo — it is the only one that needs to see both the YAML source and the
published JSON, which is exactly the monorepo's job.

```python
"""The plugin ships JSON because it has no YAML parser. The YAML is still the
source of truth, so the shipped copy must be exactly what rendering produces —
otherwise Lite runs a chain that Kraft no longer has.

This test cannot travel to the kraft-lite repo: it is the seam between the two,
and the seam belongs here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "kraft-lite"
sys.path.insert(0, str(ROOT / "dev"))

import build_lite_chain as build_chain  # noqa: E402


def test_rendered_artifact_matches_the_committed_one():
    rendered = build_chain.render(ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml")
    committed = json.loads((PLUGIN / "chains" / "default.json").read_text())
    assert rendered == committed, "run `just lite-build` — chain YAML and shipped JSON have drifted"


def test_every_node_keeps_its_hooks_and_gate():
    rendered = build_chain.render(ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml")
    by_id = {n["id"]: n for n in rendered["nodes"]}
    assert by_id["spec"]["tasks"] == ["on.spec.requested"]
    assert by_id["spec"]["gate_after"] == "spec_approval"
    assert by_id["verify"]["fix_loop"] == "verify_fix_loop"
    assert by_id["env_setup"]["gate_after"] is None


def test_the_fix_loop_cap_comes_along():
    rendered = build_chain.render(ROOT / "templates" / "default.yaml", ROOT / "templates" / "policy.yaml")
    assert rendered["loops"]["verify_fix_loop"]["attempts"] == 3
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/kraft_lite_artifact_test.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_lite_chain'`

- [ ] **Step 3: Write the renderer and generate the artifact**

Create `dev/build_lite_chain.py`. It uses PyYAML, which this repo has and the
published plugin must never require — which is precisely why it lives out here
rather than inside `plugins/kraft-lite/`.

```python
"""Render a Kraft chain template to the JSON the Lite plugin ships.

The plugin's helper is stdlib-only and cannot read YAML. Hand-copying the node
list into the plugin is the drift this file exists to prevent: the YAML stays the
one source, `just lite-build` regenerates, and a test fails if they disagree.

Deliberately outside `plugins/kraft-lite/`: that directory is published as a
standalone repo and may not depend on this one, or on PyYAML.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def render(chain_yaml: Path, policy_yaml: Path) -> dict:
    chain = yaml.safe_load(chain_yaml.read_text())
    policy = yaml.safe_load(policy_yaml.read_text())
    loops = policy.get("loops") or {}
    return {
        "id": chain["id"],
        "nodes": [
            {
                "id": node["id"],
                "tasks": list(node.get("tasks") or []),
                "gate_after": node.get("gate_after"),
                "fix_loop": node.get("fix_loop"),
            }
            for node in chain["nodes"]
        ],
        # Only the caps this chain can reach, and only `attempts`: Lite ignores
        # `wall_clock_s` because an attended session has a human watching the
        # clock (spec §8). Dropping it here is the one place that is visible.
        "loops": {
            name: {"attempts": int(loops[name]["attempts"])}
            for name in {n.get("fix_loop") for n in chain["nodes"]} - {None}
            if name in loops
        },
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = root / "plugins" / "kraft-lite" / "chains" / "default.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    artifact = render(root / "templates" / "default.yaml", root / "templates" / "policy.yaml")
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {out}")
```

Then generate it:

```bash
uv run python dev/build_lite_chain.py
```

- [ ] **Step 4: Add the `just` recipe and widen `testpaths`**

Add to `justfile`, next to the other build recipes:

```just
# Regenerate the Lite plugin's chain artifact from the YAML templates.
lite-build:
    uv run python dev/build_lite_chain.py
```

The plugin's own tests live inside the published directory, so this repo's suite
has to be told to look there. In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "plugins/kraft-lite/tests"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/kraft_lite_artifact_test.py -q`
Expected: 3 passed

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add justfile pyproject.toml dev/build_lite_chain.py plugins/kraft-lite/chains/default.json tests/kraft_lite_artifact_test.py
git commit -m "feat(lite): render the chain template to a shipped JSON artifact"
```

---

### Task 2: Records and the JSONL backend

The state machine, and the backend that needs no `bd`. Everything here is pure
functions over lists of dicts, so it tests without touching a database.

**Files:**
- Create: `plugins/kraft-lite/kl.py`
- Test: `plugins/kraft-lite/tests/test_records.py`

**Interfaces:**
- Consumes: the chain artifact dict from Task 1.
- Produces, all in `kl`:
  - `materialize(chain: dict, title: str, chain_id: str) -> list[dict]`
  - `read_jsonl(path: Path) -> list[dict]` — last write per id wins
  - `append_jsonl(path: Path, records: list[dict]) -> None`
  - `current(records: list[dict]) -> dict | None` — the node to act on
  - `label_value(record: dict, prefix: str) -> str | None`
  - `set_label(record: dict, prefix: str, value: str) -> dict` — returns a new record
  - `attempts(record: dict) -> int`
  - `NODE_LABEL = "kraft-node:"`, `GATE_LABEL = "kraft-gate:"`, `ATTEMPT_LABEL = "kraft-attempt:"`, `CHAIN_LABEL = "kraft-chain:"`

- [ ] **Step 1: Write the failing test**

Create `plugins/kraft-lite/tests/__init__.py` (empty) and `plugins/kraft-lite/tests/test_records.py`:

```python
"""State is a list of bd-shaped issue records. These are the rules that decide
which node runs next, so they are the part that must not be prose."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


@pytest.fixture
def chain():
    return json.loads((PLUGIN / "chains" / "default.json").read_text())


def test_materialize_makes_one_record_per_node_plus_an_epic(chain):
    records = kl.materialize(chain, "Add a flag", "kl-abc123")
    assert records[0]["id"] == "kl-abc123"
    assert records[0]["title"] == "Add a flag"
    assert f"{kl.CHAIN_LABEL}default" in records[0]["labels"]
    assert len(records) == len(chain["nodes"]) + 1


def test_nodes_are_chained_by_dependency(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    nodes = records[1:]
    assert nodes[0]["dependencies"] == ["kl-abc123"]
    for earlier, later in zip(nodes, nodes[1:], strict=False):
        assert later["dependencies"] == [earlier["id"]]


def test_every_node_record_carries_its_node_id(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    labelled = [kl.label_value(r, kl.NODE_LABEL) for r in records[1:]]
    assert labelled == [n["id"] for n in chain["nodes"]]


def test_current_is_the_first_node_whose_dependencies_are_closed(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    records[0]["status"] = "closed"
    assert kl.label_value(kl.current(records), kl.NODE_LABEL) == "spec"

    records[1]["status"] = "closed"
    assert kl.label_value(kl.current(records), kl.NODE_LABEL) == "plan"


def test_current_returns_a_blocked_node_rather_than_skipping_it(chain):
    """A gate must stop the walk, not get stepped over."""
    records = kl.materialize(chain, "t", "kl-abc123")
    records[0]["status"] = "closed"
    records[1]["status"] = "blocked"
    node = kl.current(records)
    assert node["status"] == "blocked"
    assert kl.label_value(node, kl.NODE_LABEL) == "spec"


def test_current_is_none_when_everything_is_closed(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    for r in records:
        r["status"] = "closed"
    assert kl.current(records) is None


def test_attempts_start_at_zero_and_a_new_label_replaces_the_old(chain):
    record = kl.materialize(chain, "t", "kl-abc123")[1]
    assert kl.attempts(record) == 0
    record = kl.set_label(record, kl.ATTEMPT_LABEL, "1")
    record = kl.set_label(record, kl.ATTEMPT_LABEL, "2")
    assert kl.attempts(record) == 2
    assert len([x for x in record["labels"] if x.startswith(kl.ATTEMPT_LABEL)]) == 1


def test_jsonl_round_trips_and_the_last_write_wins(tmp_path, chain):
    path = tmp_path / "chain.jsonl"
    records = kl.materialize(chain, "t", "kl-abc123")
    kl.append_jsonl(path, records)
    updated = dict(records[1], status="closed")
    kl.append_jsonl(path, [updated])

    loaded = kl.read_jsonl(path)
    assert len(loaded) == len(records)
    assert [r["id"] for r in loaded] == [r["id"] for r in records], "order must survive"
    assert loaded[1]["status"] == "closed"


def test_read_jsonl_of_a_missing_file_is_empty(tmp_path):
    assert kl.read_jsonl(tmp_path / "nope.jsonl") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugins/kraft-lite/tests/test_records.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kl'`

- [ ] **Step 3: Write the implementation**

Create `plugins/kraft-lite/kl.py`:

```python
"""Kraft Lite's only code: chain state records, and environment detection.

Stdlib only, on purpose. This file ships inside a plugin directory that may be
dropped into a repo which has never installed Kraft, or Python packages at all
beyond the interpreter. Anything needing a dependency belongs in the skills as
prose, or in a dev-time script that is not shipped.

Records are `bd export` shaped, so the fallback file and a bd database hold the
same thing and `bd import` is the whole migration.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

CHAIN_LABEL = "kraft-chain:"
NODE_LABEL = "kraft-node:"
GATE_LABEL = "kraft-gate:"
ATTEMPT_LABEL = "kraft-attempt:"


def new_chain_id() -> str:
    return f"kl-{uuid.uuid4().hex[:8]}"


def materialize(chain: dict, title: str, chain_id: str) -> list[dict]:
    """The epic, then one record per node, linked so each waits on the last."""
    epic = {
        "_type": "issue",
        "id": chain_id,
        "title": title,
        "status": "open",
        "description": f"Kraft Lite chain run of template {chain['id']!r}.",
        "labels": [f"{CHAIN_LABEL}{chain['id']}"],
        "dependencies": [],
    }
    records = [epic]
    previous = chain_id
    for node in chain["nodes"]:
        node_id = f"{chain_id}.{node['id']}"
        records.append(
            {
                "_type": "issue",
                "id": node_id,
                "title": node["id"],
                "status": "open",
                "description": "hooks: " + ", ".join(node["tasks"]),
                "labels": [f"{NODE_LABEL}{node['id']}"],
                "dependencies": [previous],
            }
        )
        previous = node_id
    return records


def label_value(record: dict | None, prefix: str) -> str | None:
    if record is None:
        return None
    for label in record.get("labels", []):
        if label.startswith(prefix):
            return label[len(prefix) :]
    return None


def set_label(record: dict, prefix: str, value: str) -> dict:
    """Return a copy with exactly one label under `prefix`."""
    labels = [x for x in record.get("labels", []) if not x.startswith(prefix)]
    return dict(record, labels=[*labels, f"{prefix}{value}"])


def attempts(record: dict) -> int:
    return int(label_value(record, ATTEMPT_LABEL) or 0)


def current(records: list[dict]) -> dict | None:
    """The node to act on: the first not-closed record whose dependencies are all
    closed. A blocked node is returned rather than skipped — a gate stops the
    walk, and returning the node behind it is how the caller learns which gate."""
    closed = {r["id"] for r in records if r["status"] == "closed"}
    for record in records:
        if record["status"] == "closed":
            continue
        if all(dep in closed for dep in record.get("dependencies", [])):
            return record
        return None
    return None


def read_jsonl(path: Path) -> list[dict]:
    """Last write per id wins; first-seen order is preserved."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    merged: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        merged[record["id"]] = record
    return list(merged.values())


def append_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
```

Note the epic: `current` returns it first (it is open with no dependencies), which
is why `start` closes the epic immediately after materializing — it is a container,
not a step. That happens in Task 3.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests/test_records.py -q`
Expected: 9 passed

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add plugins/kraft-lite
git commit -m "feat(lite): chain records and the no-bd JSONL backend"
```

---

### Task 3: The CLI surface and the `bd` backend

Same operations, two stores. The skills only ever call the CLI, so which backend
is live never reaches the prose.

**Files:**
- Modify: `plugins/kraft-lite/kl.py`
- Test: `plugins/kraft-lite/tests/test_backend_bd.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces:
  - `backend(root: Path) -> str` — `"bd"` or `"jsonl"`
  - `Store` protocol implemented by `JsonlStore(path)` and `BdStore(run)`, both with `load() -> list[dict]` and `write(records: list[dict]) -> None`
  - `store_for(root: Path) -> Store`
  - CLI verbs: `start --title T [--chain PATH]`, `state`, `close`, `gate --name N`, `attempt`, `approve`, `reject --note N`. The `detect` verb is wired in Task 4, beside the function it calls — wiring it here leaves an undefined name that ruff rejects.
  - `state` prints JSON: `{"chain_id", "node", "hooks", "gate", "attempt", "cap", "status", "backend"}` where `node` is the node id from the chain (not the record id) and `status` is `open`, `blocked` or `done`.

- [ ] **Step 1: Write the failing test**

Create `plugins/kraft-lite/tests/test_backend_bd.py`:

```python
"""Two stores, one format. The bd store shells out; the test drives it with a
fake `run` so it never needs a database, only the command shapes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


def test_backend_is_jsonl_without_a_beads_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    assert kl.backend(tmp_path) == "jsonl"


def test_backend_is_jsonl_when_bd_exists_but_the_repo_has_no_beads(tmp_path, monkeypatch):
    monkeypatch.setattr(kl.shutil, "which", lambda name: "/usr/bin/bd")
    assert kl.backend(tmp_path) == "jsonl"


def test_backend_is_bd_when_both_are_present(tmp_path, monkeypatch):
    monkeypatch.setattr(kl.shutil, "which", lambda name: "/usr/bin/bd")
    (tmp_path / ".beads").mkdir()
    assert kl.backend(tmp_path) == "bd"


def test_bd_store_loads_through_export(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        payload = '{"_type":"issue","id":"kl-1","title":"t","status":"open","labels":[],"dependencies":[]}'
        return subprocess.CompletedProcess(cmd, 0, stdout=payload + "\n", stderr="")

    store = kl.BdStore(run=fake_run)
    records = store.load()
    assert records[0]["id"] == "kl-1"
    assert calls[0][:2] == ["bd", "export"]


def test_bd_store_writes_through_import(tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["stdin"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    kl.BdStore(run=fake_run).write([{"_type": "issue", "id": "kl-1", "status": "closed"}])
    assert seen["cmd"][:2] == ["bd", "import"]
    assert json.loads(seen["stdin"].strip())["status"] == "closed"


def test_bd_failure_is_reported_not_swallowed():
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="dolt is angry")

    try:
        kl.BdStore(run=fake_run).load()
    except SystemExit as exit_:
        assert "dolt is angry" in str(exit_)
    else:
        raise AssertionError("a failing bd must stop the chain, not return an empty one")


def test_start_then_state_reports_the_first_node(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "Add a flag"])
    capsys.readouterr()

    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "spec"
    assert state["hooks"] == ["on.spec.requested"]
    assert state["gate"] == "spec_approval"
    assert state["status"] == "open"
    assert state["backend"] == "jsonl"


def test_close_advances_and_gate_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    kl.main(["gate", "--name", "spec_approval"])
    capsys.readouterr()

    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "blocked"
    assert state["node"] == "spec"

    kl.main(["approve"])
    capsys.readouterr()
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["node"] == "plan"


def test_attempt_increments_and_reports_the_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    for node in ["spec", "plan", "chain_review", "env_setup", "implementation"]:
        kl.main(["approve"]) if node in {"spec", "plan", "chain_review"} else kl.main(["close"])
    capsys.readouterr()

    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "verify"
    assert state["cap"] == 3

    for expected in (1, 2, 3):
        kl.main(["attempt"])
        assert json.loads(capsys.readouterr().out)["attempt"] == expected

    kl.main(["attempt"])
    assert json.loads(capsys.readouterr().out)["over_cap"] is True


def test_reject_reopens_the_node_and_keeps_the_note(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    kl.main(["gate", "--name", "spec_approval"])
    kl.main(["reject", "--note", "scope is wrong"])
    capsys.readouterr()

    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "spec"
    assert state["status"] == "open"
    assert "scope is wrong" in state["note"]
```

Note the gate semantics the tests lock in: `gate --name` blocks the *current*
node, `approve` closes it and moves on, `reject` reopens it with the note.
`close` is the no-gate path.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugins/kraft-lite/tests/test_backend_bd.py -q`
Expected: FAIL — `AttributeError: module 'kl' has no attribute 'shutil'`

- [ ] **Step 3: Write the implementation**

Append to `plugins/kraft-lite/kl.py` (and add `argparse`, `shutil`, `subprocess`,
`sys` to the imports at the top):

```python
NOTE_PREFIX = "Rejected: "
STATE_DIR = ".kraft-lite"


def backend(root: Path) -> str:
    """bd only when it is both installed and initialized here. An installed bd
    with no `.beads` in the repo would mean writing this chain into whatever
    database bd resolves from the cwd — not ours to guess."""
    if shutil.which("bd") and (root / ".beads").is_dir():
        return "bd"
    return "jsonl"


class JsonlStore:
    """ponytail: whole-file read and a linear scan, no locking. A chain is a dozen
    records, so there is nothing to index; two sessions racing one chain is the
    real limit, and the fix for that is `bd`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        return read_jsonl(self.path)

    def write(self, records: list[dict]) -> None:
        append_jsonl(self.path, records)


class BdStore:
    def __init__(self, run=subprocess.run) -> None:
        self.run = run

    def _bd(self, args: list[str], stdin: str | None = None):
        result = self.run(
            ["bd", *args], capture_output=True, text=True, input=stdin
        )
        if result.returncode != 0:
            # A store that fails quietly hands the walk a chain with no history,
            # and the next node runs as if nothing had happened.
            raise SystemExit(f"kraft-lite: bd {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def load(self) -> list[dict]:
        out = self._bd(["export", "-o", "-"]).stdout
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def write(self, records: list[dict]) -> None:
        payload = "".join(json.dumps(r) + "\n" for r in records)
        self._bd(["import", "-"], stdin=payload)


def store_for(root: Path):
    if backend(root) == "bd":
        return BdStore()
    return JsonlStore(root / STATE_DIR / "chain.jsonl")


def _chain(path: Path | None) -> dict:
    path = path or Path(__file__).parent / "chains" / "default.json"
    return json.loads(path.read_text())


def _ours(records: list[dict]) -> list[dict]:
    """bd holds a whole project's issues; only this chain's belong to the walk.
    Node ids are `<chain-id>.<node-id>`, so the prefix is the whole test.

    ponytail: one chain run per repo at a time — a second `start` would leave two
    epics and `current` would walk the older one. Upgrade path is a `--chain-id`
    argument on every verb, worth adding the first time somebody wants two.
    """
    chain_ids = {r["id"] for r in records if label_value(r, CHAIN_LABEL)}
    return [r for r in records if r["id"].split(".")[0] in chain_ids]


def _state(root: Path, chain: dict) -> dict:
    store = store_for(root)
    records = _ours(store.load())
    node_record = current(records)
    epic = next((r for r in records if label_value(r, CHAIN_LABEL)), None)
    if node_record is None:
        return {
            "chain_id": epic["id"] if epic else None,
            "node": None,
            "hooks": [],
            "gate": None,
            "attempt": 0,
            "cap": None,
            "status": "done",
            "note": "",
            "backend": backend(root),
        }
    node_id = label_value(node_record, NODE_LABEL)
    definition = next((n for n in chain["nodes"] if n["id"] == node_id), {})
    loop = definition.get("fix_loop")
    note = node_record.get("description", "")
    return {
        "chain_id": epic["id"] if epic else None,
        "node": node_id,
        "hooks": definition.get("tasks", []),
        "gate": definition.get("gate_after"),
        "attempt": attempts(node_record),
        "cap": chain["loops"].get(loop, {}).get("attempts") if loop else None,
        "status": node_record["status"],
        "note": note[note.find(NOTE_PREFIX) :] if NOTE_PREFIX in note else "",
        "backend": backend(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kl")
    sub = parser.add_subparsers(dest="verb", required=True)

    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--chain", type=Path, default=None)
    sub.add_parser("state")
    sub.add_parser("close")
    sub.add_parser("approve")
    sub.add_parser("attempt")
    gate = sub.add_parser("gate")
    gate.add_argument("--name", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--note", required=True)

    args = parser.parse_args(argv)
    root = Path.cwd()
    chain = _chain(getattr(args, "chain", None))
    store = store_for(root)

    if args.verb == "detect":
        print(json.dumps(detect(root), indent=2))
        return 0

    if args.verb == "start":
        chain_id = new_chain_id()
        records = materialize(chain, args.title, chain_id)
        # The epic is a container, not a step: close it now so the walk starts at
        # the first real node.
        records[0]["status"] = "closed"
        store.write(records)
        print(json.dumps({"chain_id": chain_id, "backend": backend(root)}))
        return 0

    records = _ours(store.load())
    node_record = current(records)
    if node_record is None and args.verb != "state":
        raise SystemExit("kraft-lite: no open node — the chain is finished or was never started")

    if args.verb == "state":
        print(json.dumps(_state(root, chain), indent=2))
        return 0

    if args.verb == "close":
        store.write([dict(node_record, status="closed")])
    elif args.verb == "gate":
        blocked = set_label(dict(node_record, status="blocked"), GATE_LABEL, args.name)
        store.write([blocked])
    elif args.verb == "approve":
        store.write([dict(node_record, status="closed")])
    elif args.verb == "reject":
        description = node_record.get("description", "")
        store.write(
            [dict(node_record, status="open", description=f"{description}\n{NOTE_PREFIX}{args.note}")]
        )
    elif args.verb == "attempt":
        count = attempts(node_record) + 1
        store.write([set_label(node_record, ATTEMPT_LABEL, str(count))])
        state = _state(root, chain)
        state["over_cap"] = state["cap"] is not None and count > state["cap"]
        print(json.dumps(state, indent=2))
        return 0

    print(json.dumps(_state(root, chain), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run both test files to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: all passed

- [ ] **Step 5: Add the stdlib-only guard**

Append to `plugins/kraft-lite/tests/test_records.py`:

```python
def test_the_shipped_helper_imports_nothing_beyond_the_stdlib():
    """The plugin lands in repos that never installed Kraft or anything else."""
    import ast

    tree = ast.parse((PLUGIN / "kl.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), f"non-stdlib import: {imported - set(sys.stdlib_module_names)}"
```

- [ ] **Step 6: Run, lint, commit**

```bash
uv run pytest plugins/kraft-lite/tests -q
just lint
git add plugins/kraft-lite
git commit -m "feat(lite): kl CLI, gates, caps, and the bd backend"
```

---

### Task 4: Detection

What `/kraft-lite:init` runs so it can write a filled-in registry instead of
interviewing the user.

**Files:**
- Modify: `plugins/kraft-lite/kl.py`
- Test: `plugins/kraft-lite/tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `detect(root: Path) -> dict` shaped
  `{"test_command": list[str] | None, "skills": {hook: [skill-name, ...]}}`, and
  `HOOK_KEYWORDS: dict[str, tuple[str, ...]]`.

- [ ] **Step 1: Write the failing test**

Create `plugins/kraft-lite/tests/test_detect.py`:

```python
"""init writes a filled-in registry rather than asking ten questions, so the
guesses have to be right — and honest about ambiguity when it exists."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


def test_a_justfile_test_recipe_wins(tmp_path):
    (tmp_path / "justfile").write_text("build:\n    echo hi\n\ntest *ARGS:\n    pytest {{ARGS}}\n")
    (tmp_path / "Makefile").write_text("test:\n\tmake-test\n")
    assert kl.detect(tmp_path)["test_command"] == ["just", "test"]


def test_a_makefile_target_is_next(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\tbuild\n\ntest:\n\tpytest\n")
    assert kl.detect(tmp_path)["test_command"] == ["make", "test"]


def test_package_json_scripts_are_next(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    assert kl.detect(tmp_path)["test_command"] == ["npm", "test"]


def test_a_pyproject_falls_back_to_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert kl.detect(tmp_path)["test_command"] == ["pytest", "-q"]


def test_an_unrecognised_repo_reports_no_command_rather_than_guessing(tmp_path):
    assert kl.detect(tmp_path)["test_command"] is None


def test_a_makefile_without_a_test_target_is_not_a_match(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\tbuild\n")
    assert kl.detect(tmp_path)["test_command"] is None


def test_installed_skills_are_matched_to_hooks(tmp_path):
    skills = tmp_path / ".claude" / "skills" / "superpowers" / "skills"
    for name in ("brainstorming", "writing-plans", "test-driven-development"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (tmp_path / ".claude" / "skills" / "superpowers" / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "superpowers" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "superpowers"})
    )

    found = kl.detect(tmp_path)["skills"]
    assert "superpowers:brainstorming" in found["on.spec.requested"]
    assert "superpowers:writing-plans" in found["on.plan.requested"]
    assert "superpowers:test-driven-development" in found["on.implementation.start"]


def test_a_skill_outside_a_plugin_is_reported_unprefixed(tmp_path):
    plain = tmp_path / ".claude" / "skills" / "code-review"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text("---\nname: code-review\n---\n")
    found = kl.detect(tmp_path)["skills"]
    assert "code-review" in found["on.review.local.run"]


def test_every_hook_in_the_chain_gets_a_key_even_with_nothing_installed(tmp_path, monkeypatch):
    # SKILL_ROOTS includes the real ~/.claude, which on a developer machine is
    # full of skills. Without this the test asserts about their laptop.
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    hooks = {hook for node in chain["nodes"] for hook in node["tasks"]}
    found = kl.detect(tmp_path)["skills"]
    assert set(found) == hooks, "a hook with no key would leave a hole in the registry"
    assert all(candidates == [] for candidates in found.values())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q`
Expected: FAIL — `AttributeError: module 'kl' has no attribute 'detect'`

- [ ] **Step 3: Write the implementation**

Append to `plugins/kraft-lite/kl.py`:

```python
#: Which installed skill names plausibly serve which hook. Deliberately keyword
#: matching and deliberately generous: init reports every candidate and asks the
#: human only when there is more than one. A wrong guess offered as a choice is
#: cheap; a missing candidate is a hook the human has to fill in by hand.
HOOK_KEYWORDS = {
    "on.spec.requested": ("brainstorm", "spec", "requirement"),
    "on.plan.requested": ("plan",),
    "on.chain.review_ready": ("chain-review",),
    "on.env.prepare": ("worktree", "env"),
    "on.implementation.start": ("test-driven", "tdd", "implement"),
    "on.test.run": (),
    "on.review.local.run": ("review",),
    "on.mr.open": ("finishing", "branch", "pull-request", "merge-request"),
    "on.ci.poll": (),
    "on.review.mr.run": ("review",),
    "on.human_review.requested": (),
    "on.merge": ("finishing", "merge"),
}

SKILL_ROOTS = (
    Path.home() / ".claude" / "plugins",
    Path.home() / ".claude" / "skills",
    Path(".claude") / "skills",
)


def _test_command(root: Path) -> list[str] | None:
    justfile = root / "justfile"
    if justfile.is_file() and re.search(r"^test\b", justfile.read_text(), re.M):
        return ["just", "test"]
    makefile = root / "Makefile"
    if makefile.is_file() and re.search(r"^test\s*:", makefile.read_text(), re.M):
        return ["make", "test"]
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text()).get("scripts") or {}
        except ValueError:
            scripts = {}
        if "test" in scripts:
            return ["npm", "test"]
    if (root / "pyproject.toml").is_file():
        return ["pytest", "-q"]
    return None


def _installed_skills(root: Path) -> list[str]:
    """Every SKILL.md reachable, named as the agent would invoke it: prefixed
    with the plugin whose manifest encloses it, bare when there is none."""
    names: set[str] = set()
    for entry in SKILL_ROOTS:
        base = entry if entry.is_absolute() else root / entry
        if not base.is_dir():
            continue
        for skill_md in base.rglob("SKILL.md"):
            name = skill_md.parent.name
            prefix = None
            for parent in skill_md.parents:
                if (parent / ".claude-plugin" / "plugin.json").is_file():
                    try:
                        manifest = json.loads((parent / ".claude-plugin" / "plugin.json").read_text())
                    except ValueError:
                        manifest = {}
                    prefix = manifest.get("name")
                    break
                if parent == base:
                    break
            names.add(f"{prefix}:{name}" if prefix else name)
    return sorted(names)


def detect(root: Path) -> dict:
    installed = _installed_skills(root)
    skills = {
        hook: [name for name in installed if any(k in name.lower() for k in keywords)] if keywords else []
        for hook, keywords in HOOK_KEYWORDS.items()
    }
    return {"test_command": _test_command(root), "skills": skills}
```

Add `import re` to the imports at the top of the file, and add the `detect` verb
to the parser and its dispatch to `main` — Task 3 deliberately left both out.

**Append `detect` and its helpers above the `if __name__ == "__main__"` guard,
not after it.** Appending after leaves the guard calling a name defined later in
the file: every import-based test passes and running the script raises
`NameError`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: all passed

- [ ] **Step 5: Verify the hook list cannot silently drift from the chain**

Add to `plugins/kraft-lite/tests/test_detect.py`:

```python
def test_keywords_do_not_match_on_short_substrings(tmp_path, monkeypatch):
    """`pr` once matched `compress`, `improver` and `project-artifact`. A keyword
    short enough to appear inside unrelated words offers the human a menu of
    nonsense and buries the real candidate."""
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    skills = tmp_path / ".claude" / "skills"
    for name in ("compress", "claude-md-improver", "project-artifact"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    assert kl.detect(tmp_path)["skills"]["on.mr.open"] == []


def test_hook_keywords_covers_exactly_the_chains_hooks():
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    hooks = {hook for node in chain["nodes"] for hook in node["tasks"]}
    assert set(kl.HOOK_KEYWORDS) == hooks
```

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q`
Expected: all passed

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add plugins/kraft-lite
git commit -m "feat(lite): detect the test command and installed skills"
```

---

### Task 5: The plugin manifest and the five skills

The spine as prose. Everything above exists so these files can stay short.

**Files:**
- Create: `plugins/kraft-lite/.claude-plugin/plugin.json`
- Create: `plugins/kraft-lite/skills/init/SKILL.md`
- Create: `plugins/kraft-lite/skills/start/SKILL.md`
- Create: `plugins/kraft-lite/skills/next/SKILL.md`
- Create: `plugins/kraft-lite/skills/gate/SKILL.md`
- Create: `plugins/kraft-lite/skills/status/SKILL.md`
- Test: `plugins/kraft-lite/tests/test_skills.py`

**Interfaces:**
- Consumes: the `kl.py` CLI verbs from Tasks 3 and 4.
- Produces: nothing later tasks import. Task 6 runs these skills end to end.

- [ ] **Step 1: Write the failing test**

Create `plugins/kraft-lite/tests/test_skills.py`, modelled on `tests/test_chain_review_skill.py`:

```python
"""The skills are the executor, so their prose is load-bearing. These check the
things that go silently wrong: a hook named that the chain does not have, a gate
invented, a kind Lite cannot run, a missing frontmatter name."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SKILLS = ["init", "start", "next", "gate", "status"]
CHAIN = json.loads((PLUGIN / "chains" / "default.json").read_text())
HOOKS = {hook for node in CHAIN["nodes"] for hook in node["tasks"]}
GATES = {node["gate_after"] for node in CHAIN["nodes"]} - {None}


@pytest.fixture(scope="module")
def texts():
    return {name: (PLUGIN / "skills" / name / "SKILL.md").read_text() for name in SKILLS}


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_has_frontmatter_naming_itself(texts, name):
    text = texts[name]
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert f"name: {name}" in head
    assert "description:" in head


def test_the_manifest_namespaces_the_skills():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "kraft-lite", "a plugin named kraft would collide with Kraft's own"
    assert manifest["skills"] == ["./skills"]


def test_no_skill_cites_a_hook_the_chain_does_not_have(texts):
    cited = {h for text in texts.values() for h in re.findall(r"`(on\.[\w.]+)`", text)}
    assert cited, "the skills cite no hooks at all"
    assert cited <= HOOKS, f"unknown hooks: {sorted(cited - HOOKS)}"


def test_no_skill_invents_a_gate(texts):
    cited = {
        g for text in texts.values() for g in re.findall(r"`(\w*(?:approval|finalized))`", text)
    }
    assert cited <= GATES, f"unknown gates: {sorted(cited - GATES)}"


def test_the_next_skill_refuses_kraft_only_handler_kinds(texts):
    """A registry copied from Kraft will contain `agent` and `builtin`. Lite must
    say so by name rather than running something unexpected."""
    for kind in ("agent", "builtin"):
        assert f"`kind: {kind}`" in texts["next"]


def test_the_next_skill_states_the_cap_behaviour(texts):
    text = texts["next"]
    assert "over_cap" in text
    assert "escalat" in text.lower()


def test_the_gate_skill_requires_a_reason_to_reject(texts):
    assert "--note" in texts["gate"]


def test_no_skill_tells_the_agent_to_poll_or_sleep(texts):
    """An attended session that sleeps burns the human's attention on a wait."""
    for name, text in texts.items():
        assert not re.search(r"\b(sleep|poll until|wait until|loop until)\b", text, re.I), name


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_invokes_the_helper_by_plugin_root(texts, name):
    """`kl.py` is not on PATH; the skills must call it where it lives."""
    assert '"$CLAUDE_PLUGIN_ROOT/kl.py"' in texts[name]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugins/kraft-lite/tests/test_skills.py -q`
Expected: FAIL — `FileNotFoundError: .../plugins/kraft-lite/skills/init/SKILL.md`

- [ ] **Step 3: Write the manifest**

Create `plugins/kraft-lite/.claude-plugin/plugin.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "kraft-lite",
  "version": "0.1.0",
  "description": "Run a Kraft chain in this session: ordered nodes, human gates, capped fix loops. No orchestrator.",
  "skills": ["./skills"]
}
```

- [ ] **Step 4: Write the five skills**

`plugins/kraft-lite/skills/init/SKILL.md`:

```markdown
---
name: init
description: Use once per repo before running a Kraft Lite chain - detects the test command and which installed skills can serve each chain hook, then writes a registry the human can edit.
---

# Setting up Kraft Lite in this repo

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" detect` from the repo root. It prints the
test command it found and, for each hook, every installed skill that plausibly
serves it.

Write `.kraft-lite/registry.yaml` from that output. Fill every hook in. For each
one, add a comment listing the other candidates detect returned, so the human can
see what you passed over:

    on.spec.requested:
      kind: skill
      skill: superpowers:brainstorming
      prompt: Agree requirements and write a spec before any code.  # used if the skill is missing
      # also found: (none)

Rules for filling it in:

- Exactly one candidate: use it, no question.
- Two or more: ask the human, once, listing them. One message, all the ambiguous
  hooks together - not one question per hook.
- None: write `kind: prompt` with a one-line instruction describing the node's
  job. The chain still runs.
- `on.test.run` and `on.ci.poll` are `kind: subprocess`. Use the detected test
  command; `on.ci.poll` is `[gh, pr, checks]`.

Every `skill` entry gets a `prompt` sibling. A renamed or uninstalled skill then
degrades to an instruction instead of stopping the chain.

Finish by printing the path and saying it is meant to be edited and committed.
```

`plugins/kraft-lite/skills/start/SKILL.md`:

```markdown
---
name: start
description: Use when starting a new piece of work under Kraft Lite - materializes the chain's nodes as state records and runs the first node.
---

# Starting a chain

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

It prints the chain id and which backend holds the state (`bd`, or a JSONL file
under `.kraft-lite/`). Say which, so the human knows where their state lives.

If `.kraft-lite/registry.yaml` does not exist, run the `init` skill first. Do not
invent bindings.

Then invoke the `next` skill. Starting a chain and stopping before the first node
leaves the human with a state file and nothing running.
```

`plugins/kraft-lite/skills/next/SKILL.md`:

```markdown
---
name: next
description: Use to run the next node of a Kraft Lite chain, and to resume one after a gate, a new session, or a cleared context - reads its whole state from disk, so it is always safe to call.
---

# Running the next node

This skill holds no state in the conversation. Everything comes from the chain
artifact and the state records, which is what makes it survive a `/clear`.

## 1. Read the state

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state

You get `node`, `hooks`, `gate`, `attempt`, `cap`, `status` and `note`.

- `status: done` - the chain is finished. Say so and stop.
- `status: blocked` - a gate is waiting. Invoke the `gate` skill. Do not proceed.
- `note` non-empty - the node was rejected. That note leads this attempt.

## 2. Run the node's hooks, in order

For each hook in `hooks`, look it up in `.kraft-lite/registry.yaml` and dispatch:

- `kind: skill` - invoke that skill. If it is not installed, follow the entry's
  `prompt` instead and say you fell back.
- `kind: prompt` - follow the instruction inline.
- `kind: subprocess` - run the command, show its output.
- `kind: agent` or `kind: builtin` - these are Kraft's, not Lite's. Stop and tell
  the human which hook is bound to one; do not improvise a substitute.

## 3. Handle the result

All hooks succeeded:

- `gate` is set - `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" gate --name <gate>`, then
  invoke the `gate` skill. Stop.
- `gate` is null - `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" close`, then run this
  skill again for the next node.

A hook failed and `cap` is set:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" attempt

If the output has `over_cap: true`, stop the chain and escalate: show the human
every attempt's output, not a summary of it. A cap that is hit is a failure they
need the traces for. Otherwise fix the cause and re-run this node's hooks only -
not the whole chain.

A hook failed and `cap` is null: stop and report. A node with no fix loop has no
retry budget to spend.

## `on.ci.poll` never waits

It runs once and reports. If checks are still running, say so and tell the human
to invoke this skill again when they finish. Do not idle - the human is sitting
here, and their attention is the resource this whole mode is spending.
```

`plugins/kraft-lite/skills/gate/SKILL.md`:

```markdown
---
name: gate
description: Use when a Kraft Lite chain is blocked at a gate - presents what the human must decide on, then records their approval or rejection.
---

# Gates

A gate is a decision that belongs to a human. You present, they decide.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state` for the gate name, then show them
what the gate is actually about - the spec, the plan, the diff, the findings. A
gate answered without the artefact in front of the person is a gate that has
stopped meaning anything.

Then, on their answer:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>"

`--note` is required. A rejection with no reason strands whoever picks the work
up next, including you after a compaction.

After an approve, invoke the `next` skill. After a reject, invoke `next` too: the
node reopens and its note leads the retry.

Never call `approve` because the answer seemed obvious. If the human has not
answered, the gate is not answered.
```

`plugins/kraft-lite/skills/status/SKILL.md`:

```markdown
---
name: status
description: Use to report where a Kraft Lite chain has got to - which node is live, what is blocking it, how many fix attempts are spent.
---

# Where the chain is

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state

Report, in a sentence or two: the node, whether it is running or blocked at a
gate, attempts spent against the cap if there is one, and which backend holds the
state.

Read-only. Do not advance, close, or approve anything from here - that is what
`next` and `gate` are for.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: all passed

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add plugins/kraft-lite
git commit -m "feat(lite): the plugin manifest and the five namespaced skills"
```

---

### Task 6: End-to-end run, standalone packaging, publish

Prove the spine walks a whole chain, then make `plugins/kraft-lite/` a directory
that stands on its own — license, CI, install instructions — and add the recipe
that pushes it to its own GitHub repo.

**Files:**
- Create: `plugins/kraft-lite/tests/test_walk_end_to_end.py`
- Create: `plugins/kraft-lite/README.md`
- Create: `plugins/kraft-lite/LICENSE`
- Create: `plugins/kraft-lite/.github/workflows/test.yml`
- Modify: `README.md` (add a section after "Use it from an agent session")
- Modify: `justfile` (add `lite-publish`)

**Interfaces:**
- Consumes: the `kl.py` CLI from Tasks 3 and 4.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Create `plugins/kraft-lite/tests/test_walk_end_to_end.py`:

```python
"""One walk of the real chain through the real CLI, in a subprocess, in a temp
repo. The unit tests cover the rules; this covers the thing the human sees."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
CHAIN = json.loads((PLUGIN / "chains" / "default.json").read_text())


def kl(cwd: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "kl.py"), *args],
        cwd=cwd, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_a_whole_chain_walks_to_done(tmp_path):
    kl(tmp_path, "start", "--title", "Add a flag")

    for node in CHAIN["nodes"]:
        state = kl(tmp_path, "state")
        assert state["node"] == node["id"], f"expected {node['id']}, got {state['node']}"
        assert state["hooks"] == node["tasks"]
        if node["gate_after"]:
            kl(tmp_path, "gate", "--name", node["gate_after"])
            assert kl(tmp_path, "state")["status"] == "blocked"
            kl(tmp_path, "approve")
        else:
            kl(tmp_path, "close")

    assert kl(tmp_path, "state")["status"] == "done"


def test_the_state_file_is_bd_importable(tmp_path):
    kl(tmp_path, "start", "--title", "Add a flag")
    kl(tmp_path, "close")

    lines = (tmp_path / ".kraft-lite" / "chain.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    assert records, "no state was written"
    for record in records:
        assert record["_type"] == "issue"
        assert set(record) >= {"id", "title", "status", "labels", "dependencies"}


def test_a_rejection_puts_the_node_back_with_its_reason(tmp_path):
    kl(tmp_path, "start", "--title", "Add a flag")
    kl(tmp_path, "gate", "--name", "spec_approval")
    kl(tmp_path, "reject", "--note", "wrong scope")

    state = kl(tmp_path, "state")
    assert state["node"] == "spec"
    assert state["status"] == "open"
    assert "wrong scope" in state["note"]


def test_nothing_in_the_plugin_reaches_outside_the_plugin(tmp_path):
    """This directory is published as its own repo. A path that climbs out of it
    passes here and breaks the moment somebody clones the public one."""
    offenders = []
    for path in PLUGIN.rglob("*.py"):
        # This file names the patterns it looks for, so it always matches itself.
        if path == Path(__file__).resolve():
            continue
        text = path.read_text()
        if "parents[2]" in text or "../.." in text:
            offenders.append(str(path.relative_to(PLUGIN)))
    assert not offenders, f"reaches above the plugin root: {offenders}"
```

- [ ] **Step 2: Run it to verify it passes or shows a real gap**

Run: `uv run pytest plugins/kraft-lite/tests/test_walk_end_to_end.py -q`
Expected: PASS. If the walk fails, the bug is in Task 3's CLI — fix it there, not here.

- [ ] **Step 3: Add the license**

MIT, matching the decision already recorded for Kraft's own first public release.

Write `plugins/kraft-lite/LICENSE` with the verbatim, unmodified MIT text and the
correct copyright line (`Copyright (c) 2026 Omid Karami`). Do not paraphrase it,
do not reflow it — a modified license text is not the license.

Nothing in this directory is open-source until this file exists, so it lands
before the first publish, not after.

- [ ] **Step 4: Add the plugin's own CI**

Create `plugins/kraft-lite/.github/workflows/test.yml`. It runs in the published
repo, and running it green here is how you learn the standalone checkout works
before anyone else clones it:

```yaml
name: test
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
      # No requirements file on purpose: the shipped helper is stdlib-only, and
      # a dependency appearing here would be the first sign that stopped being true.
      - run: pip install pytest
      - run: pytest tests -q
```

- [ ] **Step 5: Write the plugin README**

Create `plugins/kraft-lite/README.md`:

```markdown
# Kraft Lite

Runs a chain of work inside one agent session: ordered nodes, human gates at the
points where a decision belongs to you, and fix loops that are capped instead of
endless. No service, no port, no database process.

    /kraft-lite:init            # once per repo — writes .kraft-lite/registry.yaml
    /kraft-lite:start "<work>"  # materialize the chain and run the first node
    /kraft-lite:next            # run the next node; also the resume point
    /kraft-lite:gate            # approve or reject
    /kraft-lite:status          # where the chain got to

## Install

    git clone https://github.com/<org>/kraft-lite ~/.claude/skills/kraft-lite

Or clone into `.claude/skills/` for one repo only. It needs `python3` and nothing
else — no pip install, no dependencies.

## What runs each node

`/kraft-lite:init` detects which skills you already have and writes
`.kraft-lite/registry.yaml` binding each node to one of them. Lite does not
supply a spec writer or a planner; it supplies the order, the gates, and the
caps, and calls whatever you already use. Edit that file freely — it is meant to
be read, diffed and committed.

## State

`bd` when the repo has it, otherwise `.kraft-lite/chain.jsonl` — the same
`bd export` format either way, so adopting `bd` later is `bd import`, not a
migration.

## What it does not do

Unattended execution, parallel chains, a web board, CI polling, spend caps,
cross-repo search. Lite is the attended case: one chain, in front of you,
resumable across sessions but not outliving your terminal. Those other things
need a process that keeps running when you close the lid, which is a different
piece of software.

## Tests

    pytest tests -q
```

The `<org>` placeholder is filled in at Step 7 once the repo exists.

Two constraints on this file, both load-bearing:

- **No link to anything outside `plugins/kraft-lite/`.** This repo's design and
  planning directories move to a private repo at Kraft's own release, so any such
  link is a dead one in the published repo. Rationale that matters gets inlined.
- The "different piece of software" line states the ceiling honestly without
  advertising something nobody can install yet.

- [ ] **Step 6: Add a section to the top-level README**

Insert after the "Use it from an agent session" section of `README.md`:

```markdown
## Without the orchestrator

`plugins/kraft-lite/` runs the same chain inside a single agent session — same
node list, same gates, same caps, no service. It is the attended half of Kraft:
one chain, in front of you, resumable across sessions but not outliving your
terminal. Chain and policy come from `templates/`, rendered by `just lite-build`.

That directory is published as a standalone repo by `just lite-publish`, so it
must stay self-contained: no import above `plugins/kraft-lite/`, no dependency
beyond the standard library. `dev/build_lite_chain.py` and
`tests/kraft_lite_artifact_test.py` are the two pieces that deliberately live
outside it, because they are the seam between the two repos.

See [`plugins/kraft-lite/README.md`](plugins/kraft-lite/README.md).
```

- [ ] **Step 7: Add the publish recipe**

Add to `justfile`:

```just
# The published history is REGENERATED each time and force-pushed. That is
# deliberate: this repo's own release will rewrite its history, which changes
# every commit a split derives from, and a preserved history would stop
# fast-forwarding the moment that lands.
#
# It is also why this recipe has an expiry. The day the public repo has an
# external contributor, force-pushing destroys their merge base: stop running
# this, and make the public repo the source instead.
#
# One-way. An outside PR comes back by cherry-pick into this repo, never by
# pulling into the public one.
#
# `just --list` shows the LAST comment line, so the summary goes here, not first.
# Publish plugins/kraft-lite/ to its own public repo; regenerates and force-pushes.
lite-publish:
    just lite-build
    git diff --exit-code plugins/kraft-lite/chains/default.json
    uv run pytest plugins/kraft-lite/tests -q
    test -f plugins/kraft-lite/LICENSE
    git branch -D lite-publish 2>/dev/null || true
    git subtree split --prefix=plugins/kraft-lite -b lite-publish
    git push --force lite lite-publish:main
    git branch -D lite-publish
```

Wiring the remote is a one-time human step, not part of the recipe — it names an
account and creates a public repo, and neither is something to do from a script:

```bash
gh repo create <org>/kraft-lite --public --description "Run a chain of work in one agent session: ordered nodes, human gates, capped fix loops."
git remote add lite git@github.com:<org>/kraft-lite.git
```

Then fill `<org>` into the plugin README's clone URL.

**Do not run `lite-publish` as part of this task, or any other.** It publishes to
a public repo and it force-pushes. Report that the recipe is ready and let the
human run it.

- [ ] **Step 8: Run everything and lint**

```bash
uv run pytest plugins/kraft-lite/tests tests/kraft_lite_artifact_test.py -q
just lint
```

- [ ] **Step 9: Commit**

```bash
git add plugins/kraft-lite README.md justfile
git commit -m "feat(lite): end-to-end walk, standalone packaging, publish recipe"
```

---

## Verification

After Task 6, all of this must hold:

```bash
uv run pytest plugins/kraft-lite/tests -q                  # the standalone suite
uv run pytest tests/kraft_lite_artifact_test.py -q         # the drift guard
just lint                                                  # ruff check + format
just test -q tests/test_templates.py tests/test_chain_review_skill.py  # nothing upstream broke
just lite-build && git diff --exit-code plugins/kraft-lite/chains/default.json
```

Then the standalone check, which is the one that matters for publishing — copy
the directory somewhere with no Kraft and no `uv`, and run it with bare tooling:

```bash
cp -r plugins/kraft-lite /tmp/lite-standalone
cd /tmp/lite-standalone && python3 -m pytest tests -q
```

If that needs anything installed but `pytest`, the plugin is not standalone and
the publish would ship a repo that fails on first clone.

Two manual checks no test covers:

- Install the plugin into a scratch repo, run `/kraft-lite:init`, confirm it asks
  at most one question.
- `just lite-publish` is left for the human. Publishing is public and
  irreversible; the recipe being green is not permission to run it.
