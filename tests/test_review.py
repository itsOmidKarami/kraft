from __future__ import annotations

import subprocess

from support.harness import make_repo

from kraft import review


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, files: dict[str, str]):
    for name, text in files.items():
        (repo / name).write_text(text)
        _git(repo, "add", name)
    _git(repo, "commit", "-qm", "c")
    return _git(repo, "rev-parse", "HEAD")


def test_touched_by_attributes_files_to_runs_inside_the_window(tmp_path):
    repo = make_repo(tmp_path)
    c0 = _git(repo, "rev-parse", "HEAD")
    c1 = _commit(repo, {"a.py": "1\n"})
    c2 = _commit(repo, {"a.py": "2\n", "b.py": "1\n"})
    runs = [
        {"node_id": "impl", "start_sha": c0, "end_sha": c1},
        {"node_id": "verify", "start_sha": c1, "end_sha": c2},
        {"node_id": "gate", "start_sha": c2, "end_sha": c2},  # a gate row: nothing
        {"node_id": "open", "start_sha": c2, "end_sha": None},  # unfinished: skipped
    ]
    assert review.touched_by(repo, runs, c0, c2) == {
        "a.py": ["impl", "verify"],
        "b.py": ["verify"],
    }
    assert review.touched_by(repo, runs, c1, c2) == {"a.py": ["verify"], "b.py": ["verify"]}
