"""The one test that drives the real `bd` binary.

Every other bd test fakes `subprocess.run`, which is how three defects shipped
green: the dependency shape was wrong, `bd export -o -` wrote a file instead of
stdout, and walk order was taken from the store's order. All three pass a fake
and fail a database. Skipped when bd is absent, so the plugin still tests on a
machine that has never heard of it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import kl as lite  # `kl` below is the CLI helper

PLUGIN = Path(__file__).resolve().parents[1]

# e2e("bd"): the main suite runs this in CI's e2e-cli job, not the unit job.
# The skipif stays for lite-floor, which runs this directory without
# tests/conftest.py's e2e hook.
pytestmark = [
    pytest.mark.e2e("bd"),
    pytest.mark.skipif(shutil.which("bd") is None, reason="bd is not installed"),
]


@pytest.fixture(scope="session")
def _bd_template(tmp_path_factory):
    """One `bd init`ed workspace per test-run process, handed out by `copytree`.

    `bd init` spins up Dolt (~2.6s), and doing it per test was most of this
    file's runtime. Every test here only needs a *working* bd workspace, so one
    init and a copy per test is the same thing for less than a tenth of the cost.
    """
    tpl = tmp_path_factory.mktemp("kl-bd-tpl")
    subprocess.run(["git", "init", "-q"], cwd=tpl, check=True)
    # `bd init` commits, which spawns git's detached auto-maintenance; its
    # `objects/maintenance.lock` vanishing mid-`copytree` below fails a test
    # (Kraft-0y8nq, same race as tests/support/harness.py's templates).
    for key, value in (("maintenance.auto", "false"), ("gc.auto", "0")):
        subprocess.run(["git", "config", key, value], cwd=tpl, check=True)
    init = subprocess.run(["bd", "init", "--prefix", "KL"], cwd=tpl, capture_output=True, text=True)
    if init.returncode != 0:
        pytest.skip(f"bd init failed here: {init.stderr.strip()}")
    return tpl


def test_the_bd_template_disables_git_auto_maintenance(_bd_template):
    for key, want in (("maintenance.auto", "false"), ("gc.auto", "0")):
        got = subprocess.run(
            ["git", "config", "--get", key], cwd=_bd_template, capture_output=True, text=True
        ).stdout.strip()
        assert got == want, key


@pytest.fixture
def bd_repo(tmp_path, _bd_template):
    """A throwaway bd workspace. Never the repo the suite is running in."""
    repo = tmp_path / "repo"
    shutil.copytree(_bd_template, repo)
    return repo


def kl(cwd: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "kl.py"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"kl {' '.join(args)} failed: {result.stderr}"
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_a_chain_walks_through_a_real_bd_database(bd_repo):
    started = kl(bd_repo, "start", "--title", "Add a flag")
    assert started["backend"] == "bd", "a repo with .beads must use the bd store"

    state = kl(bd_repo, "state")
    assert state["node"] == "spec", "bd export order is arbitrary; the walk must not depend on it"
    assert state["kind"] == "exec" and state["steps"], "the node's steps, dispatched"

    kl(bd_repo, "close")
    kl(bd_repo, "gate")
    assert kl(bd_repo, "state")["status"] == "blocked"
    kl(bd_repo, "approve")
    assert kl(bd_repo, "state")["node"] == "plan"

    assert not (bd_repo / "-").exists(), "bd wrote a file named '-' instead of using stdout"


def test_the_records_really_round_trip_through_bd_import(bd_repo):
    """The fallback file's whole justification is that `bd import` accepts it."""
    kl(bd_repo, "start", "--title", "Add a flag")
    exported = subprocess.run(
        ["bd", "export"], cwd=bd_repo, capture_output=True, text=True, check=True
    ).stdout
    ours = [
        line
        for line in exported.splitlines()
        if line.strip() and json.loads(line)["id"].startswith("kl-")
    ]
    assert ours, "nothing of ours survived the write"

    reimport = subprocess.run(
        ["bd", "import", "-"],
        cwd=bd_repo,
        input="\n".join(ours) + "\n",
        capture_output=True,
        text=True,
    )
    assert reimport.returncode == 0, reimport.stderr


def test_the_jsonl_fallback_file_is_importable_by_bd(tmp_path, bd_repo):
    """`bd import .kraft-lite/chain.jsonl` is the documented migration. If that
    command fails, the shared format bought nothing."""
    plain = tmp_path / "no-beads"
    plain.mkdir()
    # Its own git root: state is written at the repo root, and without this the
    # bd fixture's repo one level up would own it.
    subprocess.run(["git", "init", "-q"], cwd=plain, check=True)
    kl(plain, "start", "--title", "Add a flag")
    fallback = plain / ".kraft-lite" / "chain.jsonl"
    assert fallback.is_file()

    result = subprocess.run(
        ["bd", "import", str(fallback)], cwd=bd_repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_a_write_replaces_the_labels_bd_held(bd_repo, monkeypatch):
    """`bd import` merges a record's labels into the row it updates, so a label
    the record dropped stays unless the store removes it: a slice read as
    `in_review` forever after it merged (Kraft-lpkgd). A label holding a comma
    or a quote is removed whole, never split."""
    monkeypatch.chdir(bd_repo)
    store = lite.BdStore()
    store.write(
        [
            {
                "_type": "issue",
                "id": "kl-lbl1",
                "title": "a slice",
                "issue_type": "task",
                "status": "open",
                "labels": [
                    f"{lite.SLICE_STATUS_LABEL}waiting",
                    f"{lite.TASKS_LABEL}1,2",
                    'kraft-note:a"b',
                ],
            }
        ]
    )
    for status, tasks, note in (("running", "1,2", 'a"b'), ("merged", "1,2,3", 'c"d')):
        record = next(r for r in store.load() if r["id"] == "kl-lbl1")  # two writes in a second
        record = lite.set_label(record, lite.SLICE_STATUS_LABEL, status)
        record = lite.set_label(record, lite.TASKS_LABEL, tasks)
        store.write([lite.set_label(record, "kraft-note:", note)])

    labels = next(r for r in store.load() if r["id"] == "kl-lbl1")["labels"]
    for prefix, want in (
        (lite.SLICE_STATUS_LABEL, "merged"),
        (lite.SEQ_LABEL, "3"),
        (lite.TASKS_LABEL, "1,2,3"),
        ("kraft-note:", 'c"d'),
    ):
        assert [x for x in labels if x.startswith(prefix)] == [prefix + want], prefix


@pytest.mark.parametrize("race", ["tie", "older-copy", "a-third-writer-landed"])
def test_a_row_bd_kept_keeps_its_labels(bd_repo, monkeypatch, race):
    """bd keeps the stored row on a same-second tie with another writer that
    also changed a field (`tie`), or over an older copy (`older-copy`), but
    still merges the incoming labels in: those come back out. When a third
    writer moved the row after this one loaded it, nothing is taken back, or
    that writer's labels would go too."""
    monkeypatch.chdir(bd_repo)
    monkeypatch.setattr(lite, "_now", lambda: lite.dt.datetime(2026, 10, 22, 6, 0, 0))
    store = lite.BdStore()
    first = {
        "_type": "issue",
        "id": "kl-tie1",
        "title": "a slice",
        "issue_type": "task",
        "status": "open",
        "labels": [f"{lite.SLICE_STATUS_LABEL}waiting"],
        "updated_at": "2026-10-22T06:00:09Z" if race == "older-copy" else "2026-10-22T05:59:00Z",
    }
    store.write([first])
    loaded = store.load()
    stale = next(r for r in loaded if r["id"] == "kl-tie1")
    winner = lite.set_label(stale, "kraft-session:", "B")
    store.write([lite.set_label(winner, lite.SLICE_STATUS_LABEL, "running")])
    loser = dict(first if race == "older-copy" else stale, status="in_progress")
    # The third writer's case: both move the slice to `running`, so to this
    # writer's stale copy that label, the stamp and the counter look like its own.
    lost_status = "running" if race == "a-third-writer-landed" else "in_review"
    loser = lite.set_label(loser, lite.SLICE_STATUS_LABEL, lost_status)
    if race == "a-third-writer-landed":
        reads = iter([loaded])  # this writer's own read came before the winner's write
        real = store.load
        monkeypatch.setattr(store, "load", lambda: next(reads, None) or real())
    store.write([loser])

    row = next(r for r in lite.BdStore().load() if r["id"] == "kl-tie1")
    assert row["status"] == "open", "bd kept the winner's row"
    assert "kraft-session:B" in row["labels"]
    status = [x for x in row["labels"] if x.startswith(lite.SLICE_STATUS_LABEL)]
    assert status == [f"{lite.SLICE_STATUS_LABEL}running"]
    assert [x for x in row["labels"] if x.startswith(lite.SEQ_LABEL)], "the write counter stays"


def test_an_import_replaces_the_labels_too(bd_repo, monkeypatch):
    """`kl import` writes another machine's records through `write_raw`."""
    monkeypatch.chdir(bd_repo)
    store = lite.BdStore()
    store.write(
        [
            {
                "_type": "issue",
                "id": "kl-imp1",
                "title": "a slice",
                "issue_type": "task",
                "status": "open",
                "labels": [f"{lite.SLICE_STATUS_LABEL}in_review"],
            }
        ]
    )
    local = store.load()
    theirs = lite.set_label(
        next(r for r in local if r["id"] == "kl-imp1"), lite.SLICE_STATUS_LABEL, "merged"
    )
    store.write_raw([theirs], local)

    labels = next(r for r in store.load() if r["id"] == "kl-imp1")["labels"]
    assert [x for x in labels if x.startswith(lite.SLICE_STATUS_LABEL)] == [
        f"{lite.SLICE_STATUS_LABEL}merged"
    ]
