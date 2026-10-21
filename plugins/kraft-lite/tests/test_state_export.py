"""Export and import for the state branch: a fresh container resumes a run from
three files, and an older copy of those files never undoes newer local work."""

from __future__ import annotations

import datetime as dt
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


@pytest.mark.parametrize("newer", ["local", "exported"])
def test_import_keeps_the_copy_written_last(newer, walk_in_process, monkeypatch, tmp_path):
    """Every write inside one second, as a controller's back-to-back verbs are:
    the wall-clock stamp ties, and the later write must still win."""
    monkeypatch.setattr(kl, "_now", lambda: dt.datetime(2026, 10, 20, 10, 0, 0))
    here, there = dirs(tmp_path, "here", "there")
    old, new = tmp_path / "old", tmp_path / "new"
    run = walk_in_process.run
    walk_in_process.slice(here)
    run(here, "export", "--to", str(old))
    run(there, "import", "--from", str(old))
    run(here, "slice-set", "--slice", "a", "--status", "running")
    run(here, "export", "--to", str(new))
    target, source = (here, old) if newer == "local" else (there, new)

    result = run(target, "import", "--from", str(source))

    assert run(target, "slices")["slices"][0]["status"] == "running"
    if newer == "local":
        assert result["kept_local"] >= 1 and result["imported"] == 0


def test_every_write_moves_the_records_sequence_on(walk_in_process, tmp_path):
    run = walk_in_process.run
    walk_in_process.slice(tmp_path)

    def seq():
        [alpha] = [
            r
            for r in kl.read_jsonl(tmp_path / ".kraft-lite" / "chain.jsonl")
            if kl.label_value(r, kl.SLICE_KEY_LABEL) == "a"
        ]
        return kl.label_value(alpha, kl.SEQ_LABEL)

    assert seq() == "1"
    run(tmp_path, "slice-set", "--slice", "a", "--status", "running")
    run(tmp_path, "slice-set", "--slice", "a", "--session", "s-1")
    assert seq() == "3"


def test_import_keeps_the_later_write_when_its_clock_runs_behind(
    walk_in_process, monkeypatch, tmp_path
):
    """The controller moved machines, and the new one's clock is an hour slow:
    its write is still the later one."""
    clock = {"now": dt.datetime(2026, 10, 20, 11, 0, 0)}
    monkeypatch.setattr(kl, "_now", lambda: clock["now"])
    here, there = dirs(tmp_path, "here", "there")
    old, new = tmp_path / "old", tmp_path / "new"
    run = walk_in_process.run
    walk_in_process.slice(here)
    run(here, "slice-set", "--slice", "a", "--status", "running")
    run(here, "export", "--to", str(old))
    run(there, "import", "--from", str(old))
    clock["now"] = dt.datetime(2026, 10, 20, 10, 0, 0)
    run(there, "slice-set", "--slice", "a", "--status", "in_review")
    run(there, "export", "--to", str(new))

    run(here, "import", "--from", str(new))

    assert run(here, "slices")["slices"][0]["status"] == "in_review"


def record(id_, at=None, epic=False, updated_at=None, seq=None):
    labels = [f"{kl.CHAIN_LABEL}default"] if epic else []
    if at:
        labels.append(f"{kl.AT_LABEL}{at}")
    if seq is not None:
        labels.append(f"{kl.SEQ_LABEL}{seq}")
    out = {"_type": "issue", "id": id_, "status": "open", "labels": labels, "dependencies": []}
    return dict(out, updated_at=updated_at) if updated_at else out


@pytest.mark.parametrize(
    "local, exported, written",
    [
        pytest.param(None, record("ks-1", T0), True, id="missing_locally"),
        pytest.param(record("ks-1", T0), record("ks-1", T1), True, id="local_older"),
        pytest.param(record("ks-1", T1), record("ks-1", T0), False, id="local_newer"),
        pytest.param(record("ks-1", T0), record("ks-1", T0), False, id="same_stamp"),
        # Two writes inside one second: `kraft-at:` ties, and bd's guard, which
        # every write moves strictly forward, says which came second.
        pytest.param(
            record("ks-1", T0, updated_at=T0),
            record("ks-1", T0, updated_at="2026-10-20T10:00:01Z"),
            True,
            id="same_second_export_written_later",
        ),
        pytest.param(
            record("ks-1", T0, updated_at="2026-10-20T10:00:01Z"),
            record("ks-1", T0, updated_at=T0),
            False,
            id="same_second_local_written_later",
        ),
        # bd's guard in another form: compared as times, not as text.
        pytest.param(
            record("ks-1", T0, updated_at="2026-10-20T12:00:00+02:00"),
            record("ks-1", T0, updated_at="2026-10-20T10:00:01Z"),
            True,
            id="same_second_local_guard_with_offset",
        ),
        pytest.param(
            record("ks-1", T0, updated_at="2026-10-20T10:00:01Z"),
            record("ks-1", T0, updated_at="2026-10-20T12:00:00.5+02:00"),
            False,
            id="same_second_export_guard_with_offset",
        ),
        # The write sequence decides first: a clock that runs behind on the
        # machine that wrote last does not make its write the older one.
        pytest.param(
            record("ks-1", T1, seq=3),
            record("ks-1", T0, seq=4),
            True,
            id="skewed_clock_export_later",
        ),
        pytest.param(
            record("ks-1", T0, seq=4),
            record("ks-1", T1, seq=3),
            False,
            id="skewed_clock_local_later",
        ),
        # A record from before the sequence counts as 0.
        pytest.param(record("ks-1", T1), record("ks-1", T0, seq=1), True, id="local_unsequenced"),
        pytest.param(record("ks-1", T0, seq=1), record("ks-1", T1), False, id="export_unsequenced"),
        # Same sequence, as two machines that each wrote once since the last
        # import: the clocks decide, as before.
        pytest.param(
            record("ks-1", T0, seq=2), record("ks-1", T1, seq=2), True, id="same_seq_export_later"
        ),
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


def decision(at, answered_at=None, held=False, reported=False, seq=None):
    out = {"id": "kd-1", "at": at, "held": held, "answered_at": answered_at, "reported": reported}
    return out if seq is None else dict(out, seq=seq)


@pytest.mark.parametrize(
    "local, exported, taken",
    [
        pytest.param(None, decision(T0), True, id="missing_locally"),
        pytest.param(decision(T0), decision(T0, T1), True, id="exported_answered_later"),
        pytest.param(decision(T0, T1), decision(T0, held=True), False, id="local_answered_later"),
        pytest.param(decision(T0, held=True), decision(T0, T0), True, id="tie_export_answered"),
        pytest.param(decision(T0, T0), decision(T0, held=True), False, id="tie_local_answered"),
        pytest.param(decision(T0, held=True), decision(T0, held=True), False, id="tie_both_held"),
        # A report made in the other checkout is not made again here.
        pytest.param(
            decision(T0, T0), decision(T0, T0, reported=True), True, id="tie_export_reported"
        ),
        pytest.param(
            decision(T0, T0, reported=True), decision(T0, T0), False, id="tie_local_reported"
        ),
        # An answer after the report is news, and beats the reported held copy.
        pytest.param(
            decision(T0, held=True, reported=True), decision(T0, T0), True, id="answer_beats_report"
        ),
        # The write count decides first: an answer given where the clock runs
        # behind is still the later write, and the held copy never takes it back.
        pytest.param(
            decision(T1, held=True, seq=1),
            decision(T1, T0, seq=2),
            True,
            id="skewed_clock_export_answered",
        ),
        pytest.param(
            decision(T1, T0, seq=2),
            decision(T1, held=True, seq=1),
            False,
            id="skewed_clock_local_answered",
        ),
        # Answered here, then reported there where the clock runs behind: the
        # report is the later write, and is not made again here.
        pytest.param(
            decision(T1, T1, seq=2),
            decision(T0, T0, reported=True, seq=3),
            True,
            id="skewed_clock_export_reported",
        ),
        # Marked reported here, answered there: one write each, and the answer wins.
        pytest.param(
            decision(T1, held=True, reported=True, seq=2),
            decision(T0, T0, seq=2),
            True,
            id="same_seq_answer_beats_report",
        ),
        # An entry from before the count counts as 0.
        pytest.param(decision(T1, T1), decision(T0, T0, seq=1), True, id="local_unsequenced"),
        # Stamps compare as times, not as text, as records' do.
        pytest.param(
            decision(T0, T0, seq=2),
            decision(T0, "2026-10-20T10:00:00.5Z", seq=2),
            True,
            id="same_seq_export_answered_half_a_second_later",
        ),
        pytest.param(
            decision(T0, "2026-10-20T10:45:00Z", seq=2),
            decision(T0, "2026-10-20T12:30:00+02:00", seq=2),
            False,
            id="same_seq_export_answered_earlier_in_another_zone",
        ),
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


#: Chain ids `start` never mints, each as an export's epic would carry it. The
#: climbs are spelled `"../" * n`: the plugin's own check refuses the literal.
BAD_CHAIN_IDS = {
    "id_climbs_out": "../" * 2 + "x",
    "id_climbs_out_after_a_real_one": "kl-1234abcd/" + "../" * 3 + "x",
    "id_uppercase": "kl-1234ABCD",
    "id_too_short": "kl-1234abc",
    "id_too_long": "kl-1234abcd0",
    "id_trailing_newline": "kl-1234abcd\n",
}


@pytest.mark.parametrize(
    "problem, named",
    [
        # A mistyped path is not an export of nothing.
        pytest.param("no_directory", "no export at {out}", id="no_directory"),
        pytest.param("no_records", "no export at {out}: {out}/records.jsonl", id="no_records"),
        pytest.param("no_epic", "holds 0 chain epics", id="no_epic"),
        pytest.param("two_epics", "holds 2 chain epics", id="two_epics"),
        pytest.param("no_template", "no frozen template", id="no_template"),
        # The id names files under `.kraft-lite/`, so only the shape `start` mints
        # is taken: anything else is refused before a file is read or written.
        *[
            pytest.param(problem, f"{bad!r} is not a chain id: kl- and 8 of 0-9, a-f", id=problem)
            for problem, bad in BAD_CHAIN_IDS.items()
        ],
    ],
)
def test_import_refuses_an_export_that_names_no_single_chain(problem, named, kl_cli, tmp_path):
    (there,) = dirs(tmp_path, "there")
    out = tmp_path / "state"
    if problem != "no_directory":
        out.mkdir()
    if problem == "no_epic":
        (out / "records.jsonl").write_text("")
    chain = json.loads(kl.DEFAULT_CHAIN.read_text())
    epics = {"two_epics": ["kl-aaaaaaaa", "kl-bbbbbbbb"], "no_template": ["kl-aaaaaaaa"]}
    for chain_id in epics.get(problem, [BAD_CHAIN_IDS.get(problem)]):
        if chain_id is None:
            continue
        kl.append_jsonl(out / "records.jsonl", kl.materialize(chain, "t", chain_id))
        if problem != "no_template":
            (out / "chains").mkdir(exist_ok=True)
            # The template sits where the id points, as an export made to escape would.
            template = out / "chains" / f"{chain_id}.json"
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text(json.dumps(chain))
    before = files_under(tmp_path)

    refused = kl_cli(there, "import", "--from", str(out), ok=False)

    assert "Traceback" not in refused.stderr
    assert named.format(out=out) in refused.stderr
    assert files_under(tmp_path) == before and not (there / ".kraft-lite").exists()


@pytest.mark.parametrize("bad", BAD_CHAIN_IDS.values(), ids=BAD_CHAIN_IDS.keys())
@pytest.mark.parametrize("build", [kl._frozen_path, kl._decisions_path], ids=lambda f: f.__name__)
def test_no_path_is_built_from_a_chain_id_start_never_minted(build, bad, tmp_path):
    with pytest.raises(SystemExit) as refused:
        build(tmp_path, bad)
    assert f"{bad!r} is not a chain id" in str(refused.value)
    assert build(tmp_path, "kl-1234abcd").parent.parent == tmp_path / ".kraft-lite"


@pytest.mark.parametrize(
    "verb",
    [
        ["state"],
        ["close"],
        ["slices"],
        ["decide", "--question", "q", "--why", "w", "--answer", "a"],
        ["decisions"],
        ["export", "--to", "out"],
    ],
    ids=lambda verb: verb[0],
)
def test_a_stored_chain_id_that_start_never_minted_reaches_no_path(verb, kl_cli, tmp_path):
    """A store written by hand, or by another tool through bd, can hold any epic
    id; every verb that builds a path from it refuses before reading or writing.
    An absolute id is the one that walks: `chain_index` groups a chain under the
    id's prefix before its first `.`, so one holding `..` has no nodes."""
    (here,) = dirs(tmp_path, "here")
    evil = str(tmp_path / "evil")
    assert "." not in evil, "a `.` in the temporary path would hide the chain's nodes"
    chain = json.loads(kl.DEFAULT_CHAIN.read_text())
    records = kl.materialize(chain, "t", evil)
    records[0]["status"] = "closed"
    kl.append_jsonl(here / ".kraft-lite" / "chain.jsonl", records)
    before = files_under(tmp_path)

    refused = kl_cli(here, *verb, ok=False)

    assert f"{evil!r} is not a chain id" in refused.stderr
    assert "Traceback" not in refused.stderr
    assert files_under(tmp_path) == before


@pytest.mark.parametrize("backend", ["jsonl", "bd"])
@pytest.mark.parametrize(
    "local, written",
    [
        pytest.param(None, T0, id="no_local_row"),
        pytest.param("2026-10-20T09:00:00Z", T0, id="local_row_behind"),
        pytest.param(T0, "2026-10-20T10:00:01Z", id="local_row_tied"),
        pytest.param(T1, "2026-10-20T11:00:01Z", id="local_row_ahead"),
    ],
)
def test_write_raw_beats_the_local_row_and_keeps_kraft_at(
    backend, local, written, fake_bd, tmp_path
):
    """Past bd's guard in both stores: `bd import .kraft-lite/chain.jsonl` is the
    documented migration, and it keeps the earlier line of an id whose later line
    is not newer, while `kl.py` reads the later one."""
    exported = dict(record("ks-1", T0), updated_at=T0)
    rows = [dict(record("ks-1"), updated_at=local)] if local else []
    calls = []

    def bd(cmd, **kwargs):
        calls.append(cmd[1])
        return fake_bd(cmd, **kwargs)

    path = tmp_path / "chain.jsonl"
    store = kl.BdStore(run=bd) if backend == "bd" else kl.JsonlStore(path)
    store.write_raw([exported], rows)

    if backend == "bd":
        # The rows `import` already loaded are the ones it beats: no second export.
        assert calls == ["import"]
        [payload] = [json.loads(line) for line in fake_bd.imports[-1].splitlines()]
    else:
        [payload] = kl.read_jsonl(path)
    assert payload == dict(exported, updated_at=written)
