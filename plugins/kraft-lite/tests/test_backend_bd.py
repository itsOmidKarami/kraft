"""Two stores, one format. The bd store shells out; the test drives it with a
fake `run` so it never needs a database, only the command shapes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_bd_store_loads_through_export():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        payload = (
            '{"_type":"issue","id":"kl-1","title":"t","status":"open",'
            '"labels":[],"dependencies":[]}'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=payload + "\n", stderr="")

    store = kl.BdStore(run=fake_run)
    records = store.load()
    assert records[0]["id"] == "kl-1"
    assert calls[0][:2] == ["bd", "export"]


def test_bd_store_writes_through_import():
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
    for node in ("spec", "plan", "chain_review", "env_setup", "implementation"):
        kl.main(["approve"] if node in {"spec", "plan", "chain_review"} else ["close"])
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


def test_export_is_not_given_an_output_flag():
    """`bd export -o -` writes a FILE named `-` and puts nothing on stdout, so
    load() silently returns [] and a live chain reports itself finished."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    kl.BdStore(run=fake_run).load()
    assert "-o" not in seen["cmd"], "bd writes to stdout only when -o is absent"


def test_an_unstarted_chain_is_not_reported_as_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "unstarted"
    assert state["chain_id"] is None


def test_a_finished_chain_is_still_reported_as_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    for node in chain["nodes"]:
        kl.main(["gate", "--name", node["gate_after"]]) if node["gate_after"] else None
        kl.main(["approve"] if node["gate_after"] else ["close"])
    capsys.readouterr()

    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["status"] == "done"


def test_a_custom_chain_survives_the_verbs_that_follow_start(tmp_path, monkeypatch, capsys):
    """`--chain` is only given to `start`; every later verb must walk the same
    chain rather than falling back to the default."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "quick.json"
    custom.write_text(
        json.dumps(
            {
                "id": "quick-task",
                "nodes": [
                    {
                        "id": "env_setup",
                        "tasks": ["on.env.prepare"],
                        "gate_after": None,
                        "fix_loop": None,
                    },
                    {
                        "id": "verify",
                        "tasks": ["on.test.run"],
                        "gate_after": None,
                        "fix_loop": "verify_fix_loop",
                    },
                ],
                "loops": {"verify_fix_loop": {"attempts": 2}},
            }
        )
    )
    kl.main(["start", "--title", "t", "--chain", str(custom)])
    capsys.readouterr()

    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "env_setup"
    assert state["hooks"] == ["on.env.prepare"]

    kl.main(["close"])
    capsys.readouterr()
    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "verify"
    assert state["cap"] == 2, "the custom chain's cap, not the default's"


def test_write_stamps_a_strictly_newer_updated_at():
    """bd's upsert keeps the local row on an `updated_at` tie, and its
    granularity is one second — so gate-then-approve inside one second is
    silently dropped unless the timestamp moves."""
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("input"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    store = kl.BdStore(run=fake_run)
    record = {
        "_type": "issue",
        "id": "kl-1",
        "status": "open",
        "updated_at": "2099-01-01T00:00:00Z",
    }
    store.write([record])

    written = json.loads(seen[0].strip())
    assert written["updated_at"] == "2099-01-01T00:00:01Z", "must beat the row already stored"


def test_write_stamps_updated_at_even_when_the_record_has_none():
    def fake_run(cmd, **kwargs):
        fake_run.payload = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    kl.BdStore(run=fake_run).write([{"_type": "issue", "id": "kl-1", "status": "open"}])
    assert json.loads(fake_run.payload.strip())["updated_at"].endswith("Z")


def test_a_second_start_becomes_the_live_chain(tmp_path, monkeypatch, capsys):
    """A repo accumulates one epic per start. Walking the first one found means
    the second chain is invisible and every verb exits 'no open node'."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "first"])
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    for node in chain["nodes"]:
        if node["gate_after"]:
            kl.main(["gate", "--name", node["gate_after"]])
        kl.main(["approve"] if node["gate_after"] else ["close"])
    capsys.readouterr()
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["status"] == "done"

    kl.main(["start", "--title", "second"])
    capsys.readouterr()
    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "spec", "the second chain must become the live one"
    assert state["status"] == "open"


def test_a_node_missing_from_the_chain_fails_rather_than_reporting_no_hooks(
    tmp_path, monkeypatch, capsys
):
    """`hooks: []` tells the next skill to close and move on, so a broken run
    walks to done having executed nothing."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    capsys.readouterr()
    (tmp_path / ".kraft-lite" / "chain.json").write_text(
        json.dumps({"id": "other", "nodes": [], "loops": {}})
    )
    with pytest.raises(SystemExit) as caught:
        kl.main(["state"])
    assert "not in this chain" in str(caught.value)


def test_verbs_work_from_a_subdirectory(tmp_path, monkeypatch, capsys):
    """`Path.cwd()` alone reports `unstarted` in the middle of a live chain."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    capsys.readouterr()

    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["node"] == "spec"
