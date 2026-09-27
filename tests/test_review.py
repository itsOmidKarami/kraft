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


def test_numstat_rename_forms_resolve_to_the_new_path():
    """Kraft-dl5fl 2: `git diff --numstat` names a rename `a => b` or
    `dir/{a => b}.py`; attribution and the `nodes=` filter key on the new path."""
    assert review.new_path("src/{a => b}.py") == "src/b.py"
    assert review.new_path("old.py => new.py") == "new.py"
    assert review.new_path("src/{ => sub}/x.py") == "src/sub/x.py"
    assert review.new_path("plain.py") == "plain.py"


def test_a_renamed_file_survives_the_diff_filter(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"keep.py": "x = 1\n" * 20})
    c1 = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "keep.py", "moved.py")
    (repo / "other.py").write_text("y\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mv")
    change = review.read_change(repo, c1, head=_git(repo, "rev-parse", "HEAD"))
    paths = {review.new_path(f["path"]) for f in change.files}
    assert paths == {"moved.py", "other.py"}
    kept = review.filter_diff(change.diff, {"moved.py"})
    assert "moved.py" in kept and "other.py" not in kept
