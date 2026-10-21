"""Export and import for the state branch: a fresh container resumes a run from
three files, and an older copy of those files never undoes newer local work."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

T0, T1 = "2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z"


def views(kl_cli, cwd, chain_id):
    """Everything a resumed controller reads back."""
    verbs = ("state", "summary", "slices", "decisions")
    return {verb: kl_cli(cwd, verb, "--chain-id", chain_id) for verb in verbs}


def stored(cwd):
    return sorted(kl.read_jsonl(cwd / ".kraft-lite" / "chain.jsonl"), key=lambda r: r["id"])


def dirs(tmp_path, *names):
    made = [tmp_path / name for name in names]
    for path in made:
        path.mkdir()
    return made


@pytest.mark.parametrize("backend", ["jsonl", "bd"])
def test_a_fresh_directory_resumes_from_an_export(
    backend, walk, walk_in_process, fake_bds, monkeypatch, tmp_path
):
    here, there = dirs(tmp_path, "here", "there")
    out = tmp_path / "state"
    if backend == "bd":
        # One bd per directory, each exporting newest first, in no useful order.
        monkeypatch.setattr(kl, "store_for", lambda root: kl.BdStore(run=fake_bds[root]))
        walk = walk_in_process

        def stored_in(cwd):
            return sorted(fake_bds[cwd.resolve()].stored.values(), key=lambda r: r["id"])
    else:
        stored_in = stored
    run = walk.run
    chain_id = walk.slice(here)
    run(here, "slice-set", "--slice", "a", "--status", "running", "--session", "s-1")
    run(here, "decide", "--question", "q1", "--why", "w", "--answer", "a1", "--slice", "a")
    run(here, "decide", "--question", "q2", "--why", "w", "--held")
    assert run(here, "state")["node"] == "implementation"
    before = views(run, here, chain_id)

    run(here, "export", "--to", str(out))
    result = run(there, "import", "--from", str(out))

    assert result == {"chain_id": chain_id, "imported": len(stored_in(here)) + 2, "kept_local": 0}
    assert views(run, there, chain_id) == before
    assert before["summary"]["started"] is not None
    # Written as exported: no stamp moved, so the run's clock reads the same.
    assert stored_in(there) == stored_in(here)


def test_an_older_export_never_overwrites_newer_local_records(walk, kl_cli, tmp_path):
    out = tmp_path / "state"
    walk.slice(tmp_path)
    kl_cli(tmp_path, "export", "--to", str(out))
    kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "running")

    result = kl_cli(tmp_path, "import", "--from", str(out))

    assert kl_cli(tmp_path, "slices")["slices"][0]["status"] == "running"
    assert result["kept_local"] >= 1 and result["imported"] == 0


def record(id_, at=None, epic=False):
    labels = [f"{kl.CHAIN_LABEL}default"] if epic else []
    if at:
        labels.append(f"{kl.AT_LABEL}{at}")
    return {"_type": "issue", "id": id_, "status": "open", "labels": labels, "dependencies": []}


@pytest.mark.parametrize(
    "local, exported, written",
    [
        pytest.param(None, record("ks-1", T0), True, id="missing_locally"),
        pytest.param(record("ks-1", T0), record("ks-1", T1), True, id="local_older"),
        pytest.param(record("ks-1", T1), record("ks-1", T0), False, id="local_newer"),
        pytest.param(record("ks-1", T0), record("ks-1", T0), False, id="same_stamp"),
        pytest.param(record("ks-1", T0), record("ks-1"), False, id="export_unstamped"),
        pytest.param(record("ks-1"), record("ks-1", T0), True, id="local_unstamped"),
        pytest.param(None, record("kl-1", T0, epic=True), True, id="epic_missing_locally"),
        pytest.param(
            record("kl-1", T0, epic=True),
            record("kl-1", T1, epic=True),
            False,
            id="epic_never_rewritten",
        ),
    ],
)
def test_import_writes_a_record_only_over_an_older_local_copy(local, exported, written):
    write, kept = kl._import_records([local] if local else [], [exported])

    assert (write, kept) == (([exported], 0) if written else ([], 1))


def decision(at, answered_at=None, held=False):
    return {"id": "kd-1", "at": at, "held": held, "answered_at": answered_at}


@pytest.mark.parametrize(
    "local, exported, taken",
    [
        pytest.param(None, decision(T0), True, id="missing_locally"),
        pytest.param(decision(T0), decision(T0, T1), True, id="exported_answered_later"),
        pytest.param(decision(T0, T1), decision(T0, held=True), False, id="local_answered_later"),
        pytest.param(decision(T0, held=True), decision(T0, T0), True, id="tie_export_answered"),
        pytest.param(decision(T0, T0), decision(T0, held=True), False, id="tie_local_answered"),
        pytest.param(decision(T0, held=True), decision(T0, held=True), False, id="tie_both_held"),
    ],
)
def test_import_keeps_the_later_decision_and_an_answer_wins_a_tie(local, exported, taken):
    write, kept = kl._merge_decisions([local] if local else [], [exported])

    assert (write, kept) == (([exported], 0) if taken else ([], 1))


@pytest.mark.parametrize("where", ["answered_locally", "answered_in_export"])
def test_an_answered_decision_survives_importing_its_held_copy(where, walk, kl_cli, tmp_path):
    here, there = dirs(tmp_path, "here", "there")
    old, new = tmp_path / "old", tmp_path / "new"
    chain_id = walk.slice(here)
    held = kl_cli(here, "decide", "--question", "q", "--why", "w", "--held")
    kl_cli(here, "export", "--to", str(old))
    if where == "answered_locally":
        target, source = here, old
        kl_cli(here, "answer", "--decision", held["id"], "--answer", "yes")
    else:
        target, source = there, new
        kl_cli(there, "import", "--from", str(old))
        kl_cli(here, "answer", "--decision", held["id"], "--answer", "yes")
        kl_cli(here, "export", "--to", str(new))

    kl_cli(target, "import", "--from", str(source))

    [entry] = kl_cli(target, "decisions", "--chain-id", chain_id)
    assert (entry["held"], entry["answer"], entry["answered_by"]) == (False, "yes", "user")


def test_export_carries_the_frozen_template(walk, kl_cli, tmp_path):
    here, there = dirs(tmp_path, "here", "there")
    out = tmp_path / "state"
    default = json.loads(kl.DEFAULT_CHAIN.read_text())
    custom = {**default, "id": "short", "nodes": default["nodes"][:2]}
    (tmp_path / "short.json").write_text(json.dumps(custom))
    chain_id = kl_cli(here, "start", "--title", "t", "--chain", str(tmp_path / "short.json"))[
        "chain_id"
    ]

    kl_cli(here, "export", "--to", str(out))

    assert json.loads((out / "chains" / f"{chain_id}.json").read_text()) == custom
    kl_cli(there, "import", "--from", str(out))
    assert json.loads(kl._frozen_path(there, chain_id).read_text()) == custom
    assert kl_cli(there, "state") == kl_cli(here, "state")

    # A frozen template already here is the run's own; an export never replaces it.
    (out / "chains" / f"{chain_id}.json").write_text(json.dumps(default))
    kl_cli(there, "import", "--from", str(out))
    assert json.loads(kl._frozen_path(there, chain_id).read_text()) == custom


def files_under(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def test_export_writes_no_documents(walk, kl_cli, tmp_path):
    out = tmp_path / "state"
    chain_id = walk.slice(tmp_path)
    kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--answer", "a")
    for doc in (
        ".kraft-lite/proposals/" + chain_id + ".json",
        ".kraft-lite/messages/" + chain_id + "/a.jsonl",
        ".kraft-lite/docs/spec.md",
        "docs/superpowers/plan.md",
    ):
        (tmp_path / doc).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / doc).write_text("a document\n")

    kl_cli(tmp_path, "export", "--to", str(out))

    assert files_under(out) == ["chains/" + chain_id + ".json", "decisions.jsonl", "records.jsonl"]


def test_export_into_a_checkout_replaces_only_its_own_files(walk, kl_cli, tmp_path):
    out = tmp_path / "state"
    (out / ".git").mkdir(parents=True)
    (out / ".git" / "HEAD").write_text("ref: refs/heads/kraft-lite/state\n")
    (out / "README.md").write_text("the state branch\n")
    (out / "records.jsonl").write_text('{"id": "stale"}\n')
    chain_id = walk.slice(tmp_path)

    kl_cli(tmp_path, "export", "--to", str(out))

    assert (out / ".git" / "HEAD").read_text() == "ref: refs/heads/kraft-lite/state\n"
    assert (out / "README.md").read_text() == "the state branch\n"
    exported = sorted(kl.read_jsonl(out / "records.jsonl"), key=lambda r: r["id"])
    assert exported == stored(tmp_path)
    assert chain_id in {r["id"] for r in exported}


def test_import_works_with_two_unfinished_chains_present(walk, kl_cli, tmp_path):
    here, there = dirs(tmp_path, "here", "there")
    out = tmp_path / "state"
    chain_id = walk.slice(here)
    kl_cli(here, "decide", "--question", "q", "--why", "w", "--held")
    before = views(kl_cli, here, chain_id)
    kl_cli(here, "export", "--to", str(out))
    walk.start(there)
    walk.start(there)

    assert kl_cli(there, "import", "--from", str(out))["chain_id"] == chain_id

    assert views(kl_cli, there, chain_id) == before
    assert len(kl_cli(there, "chains")) == 3


@pytest.mark.parametrize("problem", ["no_epic", "two_epics", "no_template"])
def test_import_refuses_an_export_that_names_no_single_chain(problem, kl_cli, tmp_path):
    there, out = dirs(tmp_path, "there", "state")
    chain = json.loads(kl.DEFAULT_CHAIN.read_text())
    epics = {"no_epic": [], "two_epics": ["kl-aaaa", "kl-bbbb"], "no_template": ["kl-aaaa"]}
    for chain_id in epics[problem]:
        kl.append_jsonl(out / "records.jsonl", kl.materialize(chain, "t", chain_id))
        if problem != "no_template":
            (out / "chains").mkdir(exist_ok=True)
            (out / "chains" / f"{chain_id}.json").write_text(json.dumps(chain))

    refused = kl_cli(there, "import", "--from", str(out), ok=False)

    assert "Traceback" not in refused.stderr and "export" in refused.stderr
    assert not (there / ".kraft-lite").exists()


@pytest.mark.parametrize(
    "local, written",
    [
        pytest.param(None, T0, id="no_local_row"),
        pytest.param("2026-10-20T09:00:00Z", T0, id="local_row_behind"),
        pytest.param(T0, "2026-10-20T10:00:01Z", id="local_row_tied"),
        pytest.param(T1, "2026-10-20T11:00:01Z", id="local_row_ahead"),
    ],
)
def test_bd_write_raw_beats_the_local_row_and_keeps_kraft_at(local, written, fake_bd):
    exported = dict(record("ks-1", T0), updated_at=T0)
    if local:
        fake_bd.stored["ks-1"] = dict(record("ks-1"), updated_at=local)

    kl.BdStore(run=fake_bd).write_raw([exported])

    [payload] = [json.loads(line) for line in fake_bd.imports[-1].splitlines()]
    assert payload == dict(exported, updated_at=written)
