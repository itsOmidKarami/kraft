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
