"""`chains/default.json` is the chain kraft-lite ships. Task 11b deleted the
artifact test that watched it (the legacy template system it exercised is
gone), and nothing has validated the file since — a typo in a field kl.py
actually dereferences (`gate_after` misspelled, a `fix_loop` naming a loop that
was renamed, a task with no curated hook keywords) would only surface mid-walk,
after the earlier nodes had already run and spent the human's attention.

This is the smallest honest check: the file parses, and every field kl.py's own
code reads off it is there with the shape the code expects. Not an invented
schema — the set below is exactly what `materialize`, `_state`, `chain_summaries`
and `detect` dereference (`chain["id"]`, `chain["nodes"]`, `chain["loops"]`;
`node["id"]`, `node["tasks"]`, `node.get("gate_after")`, `node.get("fix_loop")`;
`loops[name]["attempts"]`), plus `HOOK_KEYWORDS`, the curated keyword table
`detect` uses for exactly the hooks this chain ships (see its own docstring).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

DEFAULT_CHAIN = PLUGIN / "chains" / "default.json"


@pytest.fixture(scope="module")
def chain() -> dict:
    return json.loads(DEFAULT_CHAIN.read_text())


def test_default_chain_parses_with_an_id_and_nodes(chain):
    assert chain["id"] == "default"
    assert isinstance(chain["nodes"], list)
    assert chain["nodes"]


def test_every_node_has_the_fields_kl_reads(chain):
    for node in chain["nodes"]:
        assert isinstance(node["id"], str) and node["id"]
        assert isinstance(node["tasks"], list) and node["tasks"]
        assert all(isinstance(t, str) and t for t in node["tasks"])
        assert node["gate_after"] is None or isinstance(node["gate_after"], str)
        assert node["fix_loop"] is None or isinstance(node["fix_loop"], str)


def test_node_ids_are_unique(chain):
    ids = [n["id"] for n in chain["nodes"]]
    assert len(ids) == len(set(ids))


def test_every_fix_loop_names_a_loop_this_chain_defines(chain):
    named = {n["fix_loop"] for n in chain["nodes"] if n["fix_loop"]}
    assert named <= chain["loops"].keys()


def test_every_loop_declares_a_positive_attempt_budget(chain):
    assert chain["loops"]
    for loop in chain["loops"].values():
        assert isinstance(loop["attempts"], int)
        assert loop["attempts"] > 0


def test_every_task_has_a_curated_hook_keyword_entry(chain):
    """A task with no `HOOK_KEYWORDS` entry silently falls back to splitting its
    own name into keywords — the behaviour meant for a *custom* chain's hooks,
    not one this file curated on purpose. See `_hook_keywords`'s docstring."""
    tasks = {t for n in chain["nodes"] for t in n["tasks"]}
    assert tasks <= kl.HOOK_KEYWORDS.keys()
