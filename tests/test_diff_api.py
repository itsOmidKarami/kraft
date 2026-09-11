"""GET /work-items/{wid}/diff — the review surface at a human gate.

The pair that matters here is empty-vs-broken: a reviewer who cannot tell an
empty diff from a failed one approves unreviewed code.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _write(path, text):
    path.write_text(text)


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _wait_for_completion(client, wid, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        evs = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == "work_item_completed" for e in evs):
            return
        time.sleep(0.2)
    raise AssertionError("work item never completed")


@pytest.fixture
def client(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture
def seeded_item(client, tmp_path):
    """A completed quick-task work item with a real worktree and a stamped base_ref.

    The real chain leaves two artifacts behind that carry no review signal: the
    `verify` node's `python -m pytest` run drops a `.pytest_cache/`, and the
    agent adapter writes a `.engineering/sessions/*.md` summary per session (04
    §6). The cache dir is pure noise, so it is swept here; the session summary
    is real, untracked content and is left for the endpoint to report.
    """
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "make it pass", "chain_template": "quick-task"},
    ).json()["id"]
    _wait_for_completion(client, wid)
    worktree = Path(client.get(f"/api/work-items/{wid}").json()["worktree_path"])
    shutil.rmtree(worktree / ".pytest_cache", ignore_errors=True)
    return wid


@pytest.fixture
def worktree(client, seeded_item):
    return Path(client.get(f"/api/work-items/{seeded_item}").json()["worktree_path"])


@pytest.fixture
def item_without_base_ref(client, seeded_item, tmp_path):
    """`seeded_item`, but with base_ref cleared: the null-base_ref state."""
    db_path = RunDirs(tmp_path / "run").db
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE work_items SET base_ref = NULL WHERE id = ?", (seeded_item,))
    conn.commit()
    conn.close()
    return seeded_item


def test_diff_splits_landed_commits_from_in_flight_work(client, seeded_item, worktree):
    """Kraft-nceo. The chain's own committed docs are not the change under
    review, and must not spend the viewer's open-line budget ahead of it."""
    _write(worktree / "doc.md", "landed paperwork\n")
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "land the doc"], cwd=worktree, check=True)
    _write(worktree / "calc.py", "in flight code\n")

    body = client.get(f"/api/work-items/{seeded_item}/diff").json()

    assert "in flight code" in body["diff"]
    assert "landed paperwork" not in body["diff"]
    assert "calc.py" in {f["path"] for f in body["files"]}
    assert "doc.md" not in {f["path"] for f in body["files"]}
    assert body["truncated"] is False

    assert "landed paperwork" in body["landed"]["diff"]
    assert "in flight code" not in body["landed"]["diff"]
    assert "doc.md" in {f["path"] for f in body["landed"]["files"]}
    assert any("land the doc" in c for c in body["landed"]["commits"])


def test_diff_landed_is_empty_when_nothing_is_committed(client, seeded_item, worktree):
    """A worktree whose HEAD is where the chain left it: `landed` is empty and
    the page reads exactly as it did before the split."""
    # roll the worktree back to base_ref so nothing at all is committed past it
    base = client.get(f"/api/work-items/{seeded_item}/diff").json()["base_ref"]
    subprocess.run(["git", "reset", "--hard", base], cwd=worktree, check=True)
    _write(worktree / "calc.py", "in flight code\n")

    body = client.get(f"/api/work-items/{seeded_item}/diff").json()
    assert body["landed"]["files"] == []
    assert body["landed"]["diff"] == ""
    assert body["landed"]["commits"] == []
    assert body["landed"]["truncated"] is False
    assert "in flight code" in body["diff"]


def test_diff_landed_and_in_flight_truncate_independently(
    client, seeded_item, worktree, monkeypatch
):
    """Each side gets the whole DIFF_MAX_BYTES budget and its own flag: the
    in-flight change must not be squeezed by the size of the paperwork."""
    from kraft.api.routes import artifacts

    monkeypatch.setattr(artifacts, "DIFF_MAX_BYTES", 200)
    for i in range(20):
        _write(worktree / f"landed{i}.py", "x = 1\n" * 100)
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "bulk"], cwd=worktree, check=True)
    for i in range(20):
        _write(worktree / f"flight{i}.py", "y = 2\n" * 100)
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)

    body = client.get(f"/api/work-items/{seeded_item}/diff").json()
    assert body["truncated"] is True
    assert body["landed"]["truncated"] is True
    # Each side is cut against the cap on its own, not against a shared budget:
    # both still carry a diff, and neither carries the other's files. Under one
    # combined range the landed bulk consumed the cap and the in-flight change
    # arrived as nothing but a file list.
    assert body["diff"] and body["landed"]["diff"]
    assert "landed" not in body["diff"]
    assert "flight" not in body["landed"]["diff"]
    # the file lists are never truncated, on either side
    assert {f"flight{i}.py" for i in range(20)} <= {f["path"] for f in body["files"]}
    assert {f"landed{i}.py" for i in range(20)} <= {f["path"] for f in body["landed"]["files"]}


def test_diff_keeps_a_trailing_blank_context_line(client, seeded_item, worktree):
    """git_read strips, which is right for `rev-parse` and wrong for a diff: a
    hunk whose last line is blank would lose it to the strip, and the reviewer
    would read a change one line shorter than it is.

    Uncommitted end to end (nothing landed): the added line is new to HEAD, so
    it is a genuine `+`, not a context line the split would have re-labelled.
    """
    _write(worktree / "calc.py", "changed\n\n")

    body = client.get(f"/api/work-items/{seeded_item}/diff").json()["diff"]
    # the blank line the agent added is the last line of the hunk: stripped, it
    # would arrive as a bare "+"
    assert body.endswith("+changed\n+\n"), repr(body[-40:])


def test_diff_lists_untracked_without_adding_them(client, seeded_item, worktree):
    _write(worktree / "new_file.py", "x = 1\n")
    body = client.get(f"/api/work-items/{seeded_item}/diff").json()
    # not equality: the real agent's own .engineering/sessions/*.md summary is
    # also legitimately untracked at this point.
    assert "new_file.py" in body["untracked"]
    assert "new_file.py" not in body["diff"]
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=worktree, capture_output=True, text=True
    )
    assert staged.stdout.strip() == ""  # read-only: nothing was staged


def test_diff_lists_untracked_files_inside_a_new_directory(client, seeded_item, worktree):
    # `git status --porcelain` collapses an entirely-new directory to one
    # `?? sub/` line unless asked for `-uall`; a new module directory must
    # show every file it added, not one path standing in for both.
    (worktree / "sub").mkdir()
    _write(worktree / "sub" / "a.py", "a = 1\n")
    _write(worktree / "sub" / "b.py", "b = 1\n")
    body = client.get(f"/api/work-items/{seeded_item}/diff").json()
    assert "sub/a.py" in body["untracked"]
    assert "sub/b.py" in body["untracked"]
    assert "sub/" not in body["untracked"]


def test_diff_with_null_base_ref_is_empty_not_an_error(client, item_without_base_ref):
    r = client.get(f"/api/work-items/{item_without_base_ref}/diff")
    assert r.status_code == 200
    assert r.json()["base_ref"] is None
    assert r.json()["diff"] == ""


def test_diff_404s_on_unknown_work_item(client):
    assert client.get("/api/work-items/nope/diff").status_code == 404


def test_diff_404s_when_the_worktree_is_gone(client, seeded_item, worktree):
    shutil.rmtree(worktree)
    assert client.get(f"/api/work-items/{seeded_item}/diff").status_code == 404


def test_diff_500s_when_git_fails_rather_than_returning_empty(client, seeded_item, worktree):
    # A worktree whose git metadata is broken: present on disk, unusable to git.
    (worktree / ".git").unlink()
    (worktree / ".git").mkdir()
    r = client.get(f"/api/work-items/{seeded_item}/diff")
    assert r.status_code == 500
    assert r.json().get("detail")


def test_diff_truncates_at_a_file_boundary(client, seeded_item, worktree, monkeypatch):
    from kraft.api.routes import artifacts

    monkeypatch.setattr(artifacts, "DIFF_MAX_BYTES", 200)
    for i in range(20):
        _write(worktree / f"f{i}.py", "x = 1\n" * 100)
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)

    body = client.get(f"/api/work-items/{seeded_item}/diff").json()
    assert body["truncated"] is True
    # the file list is never truncated: all 20 new files are accounted for,
    # alongside the real chain's own calc.py and .engineering changes.
    paths = {f["path"] for f in body["files"]}
    assert {f"f{i}.py" for i in range(20)} <= paths
    assert not body["diff"].rstrip().endswith("x = 1")  # cut between files, not mid-hunk


def test_truncate_bounds_a_single_file_bigger_than_the_cap():
    # `kept` is empty on the very first chunk regardless of its size, so a
    # lone oversized file must not be returned whole just because there was
    # no earlier chunk to compare it against.
    from kraft.api.routes.artifacts import _truncate_at_file_boundary

    diff = "diff --git a/big.bin b/big.bin\n" + ("x" * 50 + "\n") * 20
    assert len(diff.encode()) > 200

    result, truncated = _truncate_at_file_boundary(diff, 200)
    assert truncated is True
    assert len(result.encode()) <= 200


def test_diff_degrades_gracefully_when_base_ref_is_null_and_worktree_is_gone(
    client, item_without_base_ref, worktree
):
    # The null-base_ref population is exactly the pre-migration items, which
    # are also the likeliest to have had their worktree cleaned up: this must
    # still read as "no diff", not a 404.
    shutil.rmtree(worktree)
    r = client.get(f"/api/work-items/{item_without_base_ref}/diff")
    assert r.status_code == 200
    assert r.json()["base_ref"] is None


def test_gate_artifact_is_none_without_a_pending_gate(client, seeded_item):
    assert client.get(f"/api/work-items/{seeded_item}").json()["gate_artifact"] is None
