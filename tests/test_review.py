from __future__ import annotations

import subprocess
import types

from support.harness import make_repo

from kraft import review
from kraft.templates.models import AgentTask, SubprocessTask, TaskKind


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


def _node(node_id, tasks):
    return types.SimpleNamespace(
        id=node_id, node=None, steps=[types.SimpleNamespace(tasks=[_t(t) for t in tasks])]
    )


def _t(task):
    return types.SimpleNamespace(task=task)


#: implementation: an agent task, no skill (a "working" node); verify: a
#: subprocess task plus a skilled agent task (not "working" -- `_working`
#: only counts an unskilled agent task); check_ci: a subprocess task alone.
NODES = [
    _node(
        "implementation",
        [AgentTask(id="implement", kind=TaskKind.AGENT, harness="claude", prompt="do it")],
    ),
    _node(
        "verify",
        [
            SubprocessTask(id="run_tests", kind=TaskKind.SUBPROCESS, command="pytest"),
            AgentTask(
                id="review_it",
                kind=TaskKind.AGENT,
                harness="claude",
                prompt="review",
                skill="team:review",
            ),
        ],
    ),
    _node("check_ci", [SubprocessTask(id="wait_ci", kind=TaskKind.SUBPROCESS, command="ci wait")]),
]


def thread_on(file_path):
    return {"file_path": file_path}


def test_the_target_is_the_node_that_wrote_the_threads_file(tmp_path):
    # current = check_ci (index 2); a thread on the file implementation wrote
    repo = make_repo(tmp_path)
    c0 = _git(repo, "rev-parse", "HEAD")
    c1 = _commit(repo, {"a.py": "1\n"})
    runs = [{"node_id": "implementation", "start_sha": c0, "end_sha": c1}]
    idx, why = review.changes_target(repo, NODES, 2, [thread_on("a.py")], runs)
    assert NODES[idx].id == "implementation" and "a.py" in why


def test_whole_change_threads_fall_back_to_the_current_working_node(tmp_path):
    repo = make_repo(tmp_path)
    idx, why = review.changes_target(repo, NODES, 0, [thread_on(None)], [])
    assert NODES[idx].id == "implementation" and why == "current node"


def test_a_file_only_a_subprocess_node_touched_targets_the_earlier_working_node(tmp_path):
    """A run of `check_ci` changed b.py; the nearest earlier *working* node
    (an agent task with no skill) is `implementation`, not `check_ci` itself
    (a subprocess task alone is never "working") nor `verify` (a subprocess
    task plus a skilled agent task -- still not "working")."""
    repo = make_repo(tmp_path)
    c0 = _git(repo, "rev-parse", "HEAD")
    c1 = _commit(repo, {"a.py": "1\n"})
    c2 = _commit(repo, {"b.py": "1\n"})
    runs = [
        {"node_id": "implementation", "start_sha": c0, "end_sha": c1},
        {"node_id": "check_ci", "start_sha": c1, "end_sha": c2},
    ]
    idx, why = review.changes_target(repo, NODES, 2, [thread_on("b.py")], runs)
    assert NODES[idx].id == "implementation" and "b.py" in why


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
