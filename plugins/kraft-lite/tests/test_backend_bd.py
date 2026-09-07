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
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]
    (tmp_path / kl.STATE_DIR / "chains" / f"{chain_id}.json").write_text(
        json.dumps({"id": "other", "nodes": [], "loops": {}})
    )
    with pytest.raises(SystemExit) as caught:
        kl.main(["state"])
    message = str(caught.value)
    assert "not in this chain" in message
    assert f"chains/{chain_id}.json" in message, "the path named must be the one to restore"
    assert "chain.json is the chain" not in message, "not the pre-upgrade path"


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
    upgrade rather than dying on a missing file.

    The legacy template has to differ from the packaged default, or falling
    through to the default satisfies the assertion and pins nothing. The node id
    must be one the default does not have: `verify` looks distinctive but the
    default chain has a `verify` node too, so the fallthrough still resolves.
    """
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "quick.json"
    custom.write_text(
        json.dumps(
            {
                "id": "quick-task",
                "nodes": [
                    {
                        "id": "smoke",
                        "tasks": ["on.test.run"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
                "loops": {},
            }
        )
    )
    kl.main(["start", "--title", "t", "--chain", str(custom)])
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]

    per_chain = tmp_path / kl.STATE_DIR / "chains" / f"{chain_id}.json"
    legacy = tmp_path / kl.STATE_DIR / "chain.json"
    legacy.write_text(per_chain.read_text())
    per_chain.unlink()

    kl.main(["state"])
    node = json.loads(capsys.readouterr().out)["node"]
    assert node == "smoke", "the legacy template, not the packaged default"


def test_an_unknown_chain_id_errors_even_in_an_empty_directory(tmp_path, monkeypatch, capsys):
    """A typo'd or stale id reported as `unstarted` reads to the next skill as
    "you never started a chain", which sends the human to `start`."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        kl.main(["state", "--chain-id", "kl-bogus"])
    assert "kl-bogus" in str(caught.value)


def test_start_ignores_a_legacy_chain_json(tmp_path, monkeypatch, capsys):
    """The legacy rung exists to keep runs already in flight walking. If `start`
    reads it too, a directory upgraded from a custom chain silently runs that
    template for every future `start`, and nothing ever removes the file."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / kl.STATE_DIR).mkdir()
    (tmp_path / kl.STATE_DIR / "chain.json").write_text(
        json.dumps(
            {
                "id": "legacy-custom",
                "nodes": [
                    {
                        "id": "verify",
                        "tasks": ["on.test.run"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
                "loops": {},
            }
        )
    )
    kl.main(["start", "--title", "expecting the packaged default"])
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]
    frozen = json.loads((tmp_path / kl.STATE_DIR / "chains" / f"{chain_id}.json").read_text())
    assert frozen["id"] == "default", "a new chain starts from the packaged default"


def test_chains_lists_every_chain_with_its_title_and_node(tmp_path, monkeypatch, capsys):
    """The status skill is told to report each chain in the directory, and the
    ambiguity error only fires when two are unfinished. Without a verb there is
    no way to enumerate them at all."""
    first, second = _two_unfinished(tmp_path, monkeypatch, capsys)
    kl.main(["gate", "--name", "spec_approval", "--chain-id", first])
    capsys.readouterr()

    kl.main(["chains"])
    listed = json.loads(capsys.readouterr().out)
    by_id = {row["chain_id"]: row for row in listed}
    assert set(by_id) == {first, second}
    assert by_id[first]["title"] == "first"
    assert by_id[first]["status"] == "blocked"
    assert by_id[first]["node"] == "spec"
    assert by_id[second]["status"] == "open"


def test_chains_is_empty_in_a_fresh_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["chains"])
    assert json.loads(capsys.readouterr().out) == []


def test_a_finished_chain_is_listed_as_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "only"])
    chain_id = json.loads(capsys.readouterr().out)["chain_id"]
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    for node in chain["nodes"]:
        if node["gate_after"]:
            kl.main(["gate", "--name", node["gate_after"]])
        kl.main(["approve"] if node["gate_after"] else ["close"])
    capsys.readouterr()

    kl.main(["chains"])
    row = json.loads(capsys.readouterr().out)[0]
    assert row["chain_id"] == chain_id
    assert row["status"] == "done"
    assert row["node"] is None


def test_the_newest_chain_is_the_one_most_recently_updated(tmp_path, monkeypatch):
    """`bd export` promises neither insertion nor sorted order, so reading the
    last record as the newest is a coin flip. With every chain finished, that
    choice decides which one a `done` report describes."""
    older = {
        "_type": "issue",
        "id": "kl-older",
        "title": "older",
        "status": "closed",
        "labels": [f"{kl.CHAIN_LABEL}default"],
        "updated_at": "2026-01-01T00:00:00Z",
        "dependencies": [],
    }
    newer = dict(older, id="kl-newer", title="newer", updated_at="2026-06-01T00:00:00Z")
    # Store order puts the newer chain first, which is what bd is free to do.
    picked = kl._ours([newer, older])
    assert picked[0]["id"] == "kl-newer", "newest by timestamp, not by position"


def _walk_to(tmp_path, monkeypatch, capsys, stop_after):
    """Start a default chain and walk it until `stop_after` has been closed."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    for node in chain["nodes"]:
        if node["gate_after"]:
            kl.main(["gate", "--name", node["gate_after"]])
        kl.main(["approve"] if node["gate_after"] else ["close"])
        if node["id"] == stop_after:
            break
    capsys.readouterr()


def test_a_rejection_can_send_the_chain_back_to_an_earlier_node(tmp_path, monkeypatch, capsys):
    """The last gate rejects the code, but the node being gated only presents it.
    Without a rewind the chain re-presents the same work and the gate is
    approve-or-stall."""
    _walk_to(tmp_path, monkeypatch, capsys, "mr_checks")
    kl.main(["gate", "--name", "human_review_approval"])
    capsys.readouterr()

    kl.main(["reject", "--note", "the fix is wrong", "--from-node", "implementation"])
    capsys.readouterr()
    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "implementation", "the walk resumes where the work is"
    assert state["status"] == "open"
    assert "the fix is wrong" in state["note"]


def test_a_rewind_reopens_every_node_after_the_named_one(tmp_path, monkeypatch, capsys):
    """Reopening implementation alone would run it and jump straight back to
    human_review, because verify and mr_checks are still closed - so the new code
    would never be verified."""
    _walk_to(tmp_path, monkeypatch, capsys, "mr_checks")
    kl.main(["gate", "--name", "human_review_approval"])
    kl.main(["reject", "--note", "again", "--from-node", "implementation"])
    capsys.readouterr()

    for expected in ("implementation", "verify", "open_mr", "mr_checks"):
        kl.main(["state"])
        assert json.loads(capsys.readouterr().out)["node"] == expected
        kl.main(["close"])
        capsys.readouterr()
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["node"] == "human_review"


def test_a_rewind_clears_the_gate_and_attempt_labels(tmp_path, monkeypatch, capsys):
    """A reopened node still carrying its old gate label reports `blocked`, and a
    spent attempt count would shrink the fix loop on the retry."""
    _walk_to(tmp_path, monkeypatch, capsys, "implementation")
    kl.main(["attempt"])
    kl.main(["attempt"])
    capsys.readouterr()
    kl.main(["state"])
    assert json.loads(capsys.readouterr().out)["attempt"] == 2

    kl.main(["reject", "--note", "start over", "--from-node", "verify"])
    capsys.readouterr()
    kl.main(["state"])
    state = json.loads(capsys.readouterr().out)
    assert state["node"] == "verify"
    assert state["status"] == "open", "not blocked by a stale gate label"
    assert state["attempt"] == 0, "the fix loop gets its full budget back"


def test_a_rewind_to_an_unknown_node_names_the_nodes_this_chain_has(tmp_path, monkeypatch, capsys):
    _walk_to(tmp_path, monkeypatch, capsys, "plan")
    with pytest.raises(SystemExit) as caught:
        kl.main(["reject", "--note", "n", "--from-node", "nosuch"])
    message = str(caught.value)
    assert "nosuch" in message
    assert "implementation" in message, "the real node ids are offered"
