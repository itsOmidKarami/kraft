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
    assert kl.dep_ids(nodes[0]) == ["kl-abc123"]
    for earlier, later in zip(nodes, nodes[1:], strict=False):
        assert kl.dep_ids(later) == [earlier["id"]]


def test_dependencies_are_bd_shaped_objects_not_id_strings(chain):
    """bd's schema wants objects. Emitting strings makes `bd import` of the
    fallback file fail, which is the one promise the shared format exists for."""
    node = kl.materialize(chain, "t", "kl-abc123")[1]
    (dep,) = node["dependencies"]
    assert dep["issue_id"] == node["id"]
    assert dep["depends_on_id"] == "kl-abc123"
    assert dep["type"] == kl.DEP_TYPE


def test_walk_order_is_the_chain_not_the_stores_order(chain):
    """`bd export` returns arbitrary order; reading it as the chain's order walks
    the wrong node."""
    import random

    records = kl.materialize(chain, "t", "kl-abc123")
    shuffled = records[:]
    random.Random(0).shuffle(shuffled)
    assert [r["id"] for r in kl.ordered(shuffled)] == [r["id"] for r in records]

    for r in shuffled:
        if r["id"] in ("kl-abc123", "kl-abc123.spec"):
            r["status"] = "closed"
    assert kl.label_value(kl.current(shuffled), kl.NODE_LABEL) == "plan"


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
    extra = imported - set(sys.stdlib_module_names)
    assert not extra, f"non-stdlib import: {extra}"
