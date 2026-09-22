"""Layer 2 of Kraft-nx4id: host git never enters a repository a sandboxed
worker nested in its worktree, and Kraft can tell one is there without
entering it.

Every fixture here is benign: plain nested repositories and gitlinks with no
config of their own. What is pinned is *where* Kraft looks and *what it runs*,
not what a planted repository could do (that is test_host_git_trust.py's).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from support.harness import entry_of, make_repo

from kraft.worker import sandbox


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _nested(parent: Path, rel: str) -> tuple[Path, str]:
    """A plain repository at `parent/rel` with one commit, no config of its own."""
    path = parent / rel
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "f").write_text("x\n")
    _git(path, "add", "f")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "n")
    return path, _git(path, "rev-parse", "HEAD")


def _gitlink(repo: Path, rel: str, sha: str) -> None:
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{rel}")


def _commit(repo: Path, msg: str = "c") -> str:
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


# -- nested_repos: read from the index, never by entering the repository --


def test_an_untracked_nested_repository_is_found_with_no_commit(repo):
    _nested(repo, "vendor/x")
    assert sandbox.nested_repos(repo) == {"vendor/x": None}


def test_a_committed_gitlink_is_found_with_the_commit_it_records(repo):
    _, sha = _nested(repo, "libs/a")
    _gitlink(repo, "libs/a", sha)
    _commit(repo)
    assert sandbox.nested_repos(repo) == {"libs/a": sha}


def test_a_plain_worktree_holds_no_nested_repository(repo):
    (repo / "dir").mkdir()
    (repo / "dir" / "file").write_text("plain\n")
    assert sandbox.nested_repos(repo) == {}


def test_an_unreadable_worktree_is_none_not_empty(tmp_path):
    """A check that cannot read reads as unknown, never as clean."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert sandbox.nested_repos(plain) is None


# -- planted_repos: which of those a sandboxed item's worker made --


@pytest.mark.parametrize(
    "shape",
    ["untracked-nested-repo", "populated-gitlink", "gitlink-added-since-base"],
)
def test_a_repository_the_worker_made_is_planted(repo, shape):
    base = _git(repo, "rev-parse", "HEAD")
    nested, sha = _nested(repo, "sub")
    if shape != "untracked-nested-repo":
        _gitlink(repo, "sub", sha)
        _commit(repo)
    if shape == "gitlink-added-since-base":
        # Unpopulated: only the gitlink's being new since base makes it the worker's.
        subprocess.run(["rm", "-rf", str(nested)], check=True)
        nested.mkdir()
    assert sandbox.planted_repos(repo, base) == ["sub"]


def test_a_gitlink_base_already_had_and_left_unpopulated_is_not_planted(repo):
    nested, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    base = _commit(repo)
    subprocess.run(["rm", "-rf", str(nested)], check=True)
    nested.mkdir()
    assert sandbox.planted_repos(repo, base) == []


def test_a_gitlink_moved_since_base_is_planted(repo):
    nested, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    base = _commit(repo)
    subprocess.run(["rm", "-rf", str(nested)], check=True)
    nested.mkdir()
    _gitlink(repo, "sub", "1" * 40)
    _commit(repo, "move")
    assert sandbox.planted_repos(repo, base) == ["sub"]


def test_an_unreadable_base_is_none_not_clean(repo):
    _nested(repo, "sub")
    assert sandbox.planted_repos(repo, "0" * 40) is None


def test_planted_refusal_names_the_scope_and_every_path():
    msg = sandbox.planted_refusal("task 'build'", ["a", "b/c"])
    assert "task 'build'" in msg and "a, b/c" in msg


# -- the environment every host git inherits --


_RECURSION = {
    "submodule.recurse": "false",
    "fetch.recurseSubmodules": "false",
    "push.recurseSubmodules": "no",
    "diff.submodule": "short",
    "status.submoduleSummary": "false",
    "diff.ignoreSubmodules": "dirty",
}


def _pins(env) -> dict[str, str]:
    n = int(env["GIT_CONFIG_COUNT"])
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)}


def test_the_hardened_environment_pins_every_recursion_setting_and_the_hooks():
    env: dict[str, str] = {}
    sandbox.harden_host_git_env(env)
    assert _pins(env) == {**_RECURSION, "core.hooksPath": os.devnull, "core.fsmonitor": ""}


def test_push_lifts_only_the_hooks_path_and_keeps_every_recursion_pin(monkeypatch):
    for k in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        monkeypatch.delenv(k)
    assert _pins(sandbox.unhardened_git_env()) == {**_RECURSION, "core.fsmonitor": ""}


def test_the_hardened_environment_really_keeps_status_out_of_a_gitlink(repo):
    """The pins, read back through git itself: with a gitlink whose
    repository has uncommitted edits, `status` under the pins reports the
    gitlink clean -- it compared the recorded commit and never looked inside."""
    nested, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    _commit(repo)
    (nested / "f").write_text("edited inside\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    unpinned = subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=none"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "sub" in unpinned  # the control: an entering status sees the edit
    sandbox.harden_host_git_env(env)
    pinned = subprocess.run(
        ["git", "status", "--porcelain", sandbox.SUBMODULES_UNENTERED],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "sub" not in pinned


# -- the guard: a planted repository stops a sandboxed item, and only that --

from support.harness import v1_chain  # noqa: E402

from kraft.executor import stops  # noqa: E402
from kraft.executor.context import LaunchContext  # noqa: E402

_SANDBOX = {"kind": "docker", "image": "img"}
_NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
    }
]


def _row(repo: Path, base: str) -> dict:
    return {
        "id": "w1",
        "materialized_chain": v1_chain(_NODES, repo=str(repo)).to_json(),
        "submodules": None,
        "base_ref": base,
        "repo": str(repo),
    }


def _launch(repo: Path, sandbox: dict | None) -> LaunchContext:
    entry = {"path": str(repo)} | ({"sandbox": sandbox} if sandbox is not None else {})
    return LaunchContext(repo_entry=entry_of(entry))


def test_a_planted_repository_stops_a_sandboxed_item_naming_its_path(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _nested(repo, "vendor/x")
    with pytest.raises(RuntimeError, match="vendor/x"):
        stops.refuse_planted_repos(_row(repo, base), _launch(repo, _SANDBOX), repo)


def test_a_clean_sandboxed_worktree_goes_through(repo):
    base = _git(repo, "rev-parse", "HEAD")
    assert stops.refuse_planted_repos(_row(repo, base), _launch(repo, _SANDBOX), repo) is None


def test_an_unsandboxed_item_never_even_looks(repo, monkeypatch):
    """An unsandboxed item costs no git spawn: the planted-repo scan is not
    called at all, whatever its worktree holds."""
    base = _git(repo, "rev-parse", "HEAD")
    _nested(repo, "vendor/x")
    calls = []
    monkeypatch.setattr(sandbox, "planted_repos", lambda *a, **k: calls.append(a) or ["x"])
    assert stops.refuse_planted_repos(_row(repo, base), _launch(repo, None), repo) is None
    assert calls == []


def test_the_walk_entry_guard_delegates_a_plain_item_to_the_planted_scan(repo):
    """With no declared submodules, `refuse_sandboxed_submodules` (the walk
    and the doors) stops on a planted repository exactly as dispatch does."""
    base = _git(repo, "rev-parse", "HEAD")
    _nested(repo, "vendor/x")
    with pytest.raises(RuntimeError, match="vendor/x"):
        stops.refuse_sandboxed_submodules(_row(repo, base), _launch(repo, _SANDBOX), repo)


def test_an_unreadable_index_stops_rather_than_reads_as_clean(repo, monkeypatch):
    base = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(sandbox, "planted_repos", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="cannot read its index"):
        stops.refuse_planted_repos(_row(repo, base), _launch(repo, _SANDBOX), repo)


# -- the straggler sweep: never stages a repository the item did not declare --

import asyncio  # noqa: E402

from kraft.adapters.forge import git as forge_git  # noqa: E402


def _sweep(repo: Path, monkeypatch, mounts=()):
    calls: list[list[str]] = []
    real = forge_git.run_git

    async def recording(cwd, argv, *a, **k):
        calls.append(list(argv))
        return await real(cwd, argv, *a, **k)

    monkeypatch.setattr(forge_git, "run_git", recording)
    (repo / "work.txt").write_text("left behind\n")
    committed = asyncio.run(
        forge_git.commit_stragglers(repo, base="main", message="sweep", mounts=mounts)
    )
    return committed, calls


def test_the_sweep_leaves_an_undeclared_nested_repository_out(repo, monkeypatch):
    _nested(repo, "vendor/x")
    committed, calls = _sweep(repo, monkeypatch)
    assert committed
    tracked = _git(repo, "ls-files", "-s").splitlines()
    assert any(line.endswith("\twork.txt") for line in tracked)
    assert not any(line.endswith("\tvendor/x") for line in tracked)
    add = next(c for c in calls if "add" in c)
    assert ":(exclude,literal)vendor/x" in add


def test_the_sweep_stages_a_declared_mount_and_never_enters_it_on_status(repo, monkeypatch):
    _, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    _commit(repo)
    committed, calls = _sweep(repo, monkeypatch, mounts=("sub",))
    add = next(c for c in calls if "add" in c)
    assert ":(exclude,literal)sub" not in add
    status = next(c for c in calls if "status" in c)
    assert sandbox.SUBMODULES_UNENTERED in status


# -- Kraft-69rwp (a): no host git on a sandboxed worktree while a session is live --


class _Db:
    def __init__(self, live: list[str]):
        self.live = live

    def read(self, fn):
        return self.live


@pytest.mark.parametrize(
    ("live", "sandboxed", "refused"),
    [
        pytest.param(["s1"], True, True, id="live-and-sandboxed"),
        pytest.param([], True, False, id="sandboxed-but-no-live-session"),
        pytest.param(["s1"], False, False, id="live-but-unsandboxed"),
    ],
)
def test_host_git_waits_only_for_a_live_sandboxed_session(repo, live, sandboxed, refused):
    row = _row(repo, _git(repo, "rev-parse", "HEAD"))
    launch = _launch(repo, _SANDBOX if sandboxed else None)
    if refused:
        with pytest.raises(RuntimeError, match="the diff is available once work item w1"):
            stops.refuse_live_sandboxed_session(_Db(live), row, launch, what="the diff")
    else:
        assert stops.refuse_live_sandboxed_session(_Db(live), row, launch, what="the diff") is None
