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
        cwd=cwd,
        capture_output=True,
        text=True,
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


def test_a_finished_chain_still_summarizes(tmp_path):
    """`state` is the only other verb allowed to run once the walk is over, and
    the summary is worth nothing if it is refused at exactly the moment it is
    wanted."""
    kl(tmp_path, "start", "--title", "Add a flag")
    for _ in CHAIN["nodes"]:
        kl(tmp_path, "close")

    out = kl(tmp_path, "summary")
    assert out["status"] == "done"
    assert out["title"] == "Add a flag"
    assert [n["node"] for n in out["nodes"]] == [n["id"] for n in CHAIN["nodes"]]
    assert out["totals"]["closed"] == len(CHAIN["nodes"])
    assert out["duration_seconds"] >= 0


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


def test_nothing_in_the_plugin_reaches_outside_the_plugin():
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


def test_the_gate_verb_records_the_wait_it_opened(tmp_path):
    """The wait is only measurable if the block writes a stamp of its own: the
    approve overwrites the node's `updated_at`, so afterwards there is none."""
    kl(tmp_path, "start", "--title", "Add a flag")
    kl(tmp_path, "gate", "--name", "spec_approval")
    kl(tmp_path, "approve")

    node = kl(tmp_path, "summary")["nodes"][0]
    assert node["blocked_seconds"] is not None
    assert node["blocked_seconds"] <= node["seconds"]


def test_no_node_waits_longer_than_it_took(tmp_path):
    """`updated_at` is bd's import guard, not a clock: it is pushed forward when
    two writes land in the same second, so timing a node with it while timing the
    wait with the wall clock made a sub-second run report eight seconds of
    waiting, on nodes that reported taking none."""
    kl(tmp_path, "start", "--title", "Add a flag")
    for node in CHAIN["nodes"]:
        if node["gate_after"]:
            kl(tmp_path, "gate", "--name", node["gate_after"])
            kl(tmp_path, "approve")
        else:
            kl(tmp_path, "close")

    out = kl(tmp_path, "summary")
    for node in out["nodes"]:
        assert node["blocked_seconds"] is None or node["blocked_seconds"] <= node["seconds"], node
    assert out["totals"]["blocked_seconds"] <= out["duration_seconds"], out["totals"]


def test_a_chain_stopped_at_a_gate_reports_what_it_is_waiting_on(tmp_path):
    """The mid-run question the status skill points `summary` at: this node has
    been sitting on a human since some point, and that is the number they want."""
    kl(tmp_path, "start", "--title", "Add a flag")
    kl(tmp_path, "gate", "--name", "spec_approval")

    node = kl(tmp_path, "summary")["nodes"][0]
    assert node["status"] == "blocked"
    assert node["blocked_seconds"] is not None, "a gate held right now is still a wait"


def test_a_rewind_banks_the_wait_it_interrupts(tmp_path):
    """`reject --from-node` is how the *last* gate sends work back, so the wait it
    closes is the one a human has been sitting on longest in the whole run."""
    kl(tmp_path, "start", "--title", "Add a flag")
    kl(tmp_path, "gate", "--name", "spec_approval")
    kl(tmp_path, "reject", "--note", "redo", "--from-node", "spec")

    node = kl(tmp_path, "summary")["nodes"][0]
    assert node["blocked_seconds"] is not None, "the gate held someone, then was rewound"
