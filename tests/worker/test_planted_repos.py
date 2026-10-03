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
from support.harness import entry_of, git, make_repo

from kraft.worker import sandbox


def _nested(parent: Path, rel: str) -> tuple[Path, str]:
    """A plain repository at `parent/rel` with one commit, no config of its own."""
    path = parent / rel
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "f").write_text("x\n")
    git(path, "add", "f")
    git(path, "commit", "-q", "-m", "n")
    return path, git(path, "rev-parse", "HEAD")


def _gitlink(repo: Path, rel: str, sha: str) -> None:
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{rel}")


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
    git(repo, "commit", "-qm", "c")
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
    base = git(repo, "rev-parse", "HEAD")
    nested, sha = _nested(repo, "sub")
    if shape != "untracked-nested-repo":
        _gitlink(repo, "sub", sha)
        git(repo, "commit", "-qm", "c")
    if shape == "gitlink-added-since-base":
        # Unpopulated: only the gitlink's being new since base makes it the worker's.
        subprocess.run(["rm", "-rf", str(nested)], check=True)
        nested.mkdir()
    assert sandbox.planted_repos(repo, base) == ["sub"]


def test_a_gitlink_base_already_had_and_left_unpopulated_is_not_planted(repo):
    nested, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    git(repo, "commit", "-qm", "c")
    base = git(repo, "rev-parse", "HEAD")
    subprocess.run(["rm", "-rf", str(nested)], check=True)
    nested.mkdir()
    assert sandbox.planted_repos(repo, base) == []


def test_a_gitlink_moved_since_base_is_planted(repo):
    nested, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    git(repo, "commit", "-qm", "c")
    base = git(repo, "rev-parse", "HEAD")
    subprocess.run(["rm", "-rf", str(nested)], check=True)
    nested.mkdir()
    _gitlink(repo, "sub", "1" * 40)
    git(repo, "commit", "-qm", "move")
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
    git(repo, "commit", "-qm", "c")
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
    base = git(repo, "rev-parse", "HEAD")
    _nested(repo, "vendor/x")
    with pytest.raises(RuntimeError, match="vendor/x"):
        stops.refuse_planted_repos(_row(repo, base), _launch(repo, _SANDBOX), repo)


def test_a_clean_sandboxed_worktree_goes_through(repo):
    base = git(repo, "rev-parse", "HEAD")
    checked = stops.refuse_planted_repos(_row(repo, base), _launch(repo, _SANDBOX), repo)
    assert checked == sandbox.Checkout(repo, {})


def test_an_unsandboxed_item_never_even_looks(repo, monkeypatch):
    """An unsandboxed item costs no git spawn: the planted-repo scan is not
    called at all, whatever its worktree holds."""
    base = git(repo, "rev-parse", "HEAD")
    _nested(repo, "vendor/x")
    calls = []
    monkeypatch.setattr(sandbox, "planted_repos", lambda *a, **k: calls.append(a) or ["x"])
    assert stops.refuse_planted_repos(_row(repo, base), _launch(repo, None), repo) is None
    assert calls == []


def test_an_unreadable_repos_yaml_stops_an_item_with_members_rather_than_reads_as_no_sandbox(
    repo,
):
    """A poisoned entry (`deps._PoisonedRepoEntry`) must not read as "no
    sandbox" and wave an item with members through."""
    from kraft import config
    from kraft.api import deps

    row = _row(repo, git(repo, "rev-parse", "HEAD")) | {"submodules": '["repos/pkg"]'}
    launch = LaunchContext(repo_entry=deps._PoisonedRepoEntry(config.ConfigError("broken")))
    with pytest.raises(RuntimeError, match="cannot tell whether w1 runs sandboxed"):
        stops.refuse_planted_repos(row, launch, repo)


def test_an_unreadable_index_stops_rather_than_reads_as_clean(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
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
        forge_git.commit_stragglers(
            repo, branch="main", base="main", message="sweep", mounts=mounts
        )
    )
    return committed, calls


def test_the_sweep_leaves_an_undeclared_nested_repository_out(repo, monkeypatch):
    _nested(repo, "vendor/x")
    committed, calls = _sweep(repo, monkeypatch)
    assert committed
    tracked = git(repo, "ls-files", "-s").splitlines()
    assert any(line.endswith("\twork.txt") for line in tracked)
    assert not any(line.endswith("\tvendor/x") for line in tracked)
    add = next(c for c in calls if "add" in c)
    assert ":(exclude,literal)vendor/x" in add


def test_the_sweep_stages_a_declared_mount_and_never_enters_it_on_status(repo, monkeypatch):
    _, sha = _nested(repo, "sub")
    _gitlink(repo, "sub", sha)
    git(repo, "commit", "-qm", "c")
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
    row = _row(repo, git(repo, "rev-parse", "HEAD"))
    launch = _launch(repo, _SANDBOX if sandboxed else None)
    if refused:
        with pytest.raises(RuntimeError, match="the diff is available once work item w1"):
            stops.refuse_live_sandboxed_session(_Db(live), row, launch, what="the diff")
    else:
        assert stops.refuse_live_sandboxed_session(_Db(live), row, launch, what="the diff") is None


# -- Kraft-ju36l: a declared member is Kraft's only in the checkout Kraft made --

from support import worktree as wtree  # noqa: E402
from support.harness import make_repo_with_submodule  # noqa: E402
from support.workspace import workspace_target  # noqa: E402

_REL = "repos/pkg"


async def _member_checkout(database, run_dirs, tmp_path, *, connected=True):
    """Item w1's worktree with member `pkg` at `_REL`, made by `ensure_worktree`
    from its connected repository (or, not `connected`, the old way). Returns
    `(worktree, connected repository, what foreign_members expects)`."""
    root, sub = make_repo_with_submodule(tmp_path)
    chain = v1_chain(_NODES, repo=root, target=workspace_target({"pkg": _REL}))
    await wtree.make_item(database, root, materialized_chain=chain.to_json())
    repositories = {"pkg": entry_of({"id": "pkg", "path": str(sub)})} if connected else None
    wt = await wtree.ensure(database, run_dirs, root, repositories=repositories)
    return wt, sub, {_REL: sandbox.member_gitdirs(sub, wt, _REL)}


async def test_kraft_created_members_pass_and_their_pointer_moves_are_not_planted(
    tmp_path, database, run_dirs
):
    wt, _sub, expected = await _member_checkout(database, run_dirs, tmp_path)
    base = git(wt, "rev-parse", "HEAD")
    (wt / _REL / "work.txt").write_text("member work\n")
    git(wt / _REL, "add", "work.txt")
    git(wt / _REL, "commit", "-qm", "c")
    git(wt, "add", _REL)
    git(wt, "commit", "-qm", "move the member's pointer")

    assert sandbox.foreign_members(wt, expected) == []
    assert sandbox.planted_repos(wt, base, mounts=[_REL]) == []


async def test_a_member_whose_gitfile_names_another_gitdir_is_foreign(tmp_path, database, run_dirs):
    """The worker moves the member aside and puts a `.git` of its own at the
    mount path, naming a repository it built. The connected repository's
    admin dir still names the mount path, so only the gitfile tells."""
    wt, _sub, _ = await _member_checkout(database, run_dirs, tmp_path)
    (wt / "repos").rename(wt / "repos-moved")
    evil, _ = _nested(wt, "evil")
    (wt / _REL).mkdir(parents=True)
    (wt / _REL / ".git").write_text(f"gitdir: {evil / '.git'}\n")

    expected = {_REL: sandbox.member_gitdirs(_sub, wt, _REL)}
    assert expected[_REL] is not None
    assert sandbox.foreign_members(wt, expected) == [_REL]


async def test_a_member_whose_admin_dir_leads_to_another_common_gitdir_is_foreign(
    tmp_path, database, run_dirs
):
    """The gitfile is Kraft's, but `commondir` in the admin dir -- which the
    worker writes when it is not mounted read-only -- names a repository the
    worker built, whose config host git would then read."""
    wt, _sub, expected = await _member_checkout(database, run_dirs, tmp_path)
    evil, _ = _nested(tmp_path, "evil")
    (expected[_REL][1] / "commondir").write_text(f"{evil / '.git'}\n")

    assert sandbox.foreign_members(wt, expected) == [_REL]


async def test_a_member_reached_through_a_symlinked_parent_is_foreign(tmp_path, database, run_dirs):
    """A symlink a worker can repoint between the check and the next git.
    Here it still lands on the real member, inside the worktree, so nothing
    but the symlink itself is wrong with it."""
    wt, _sub, expected = await _member_checkout(database, run_dirs, tmp_path)
    (wt / "repos").rename(wt / "repos-real")
    (wt / "repos").symlink_to("repos-real")

    assert sandbox.foreign_members(wt, expected) == [_REL]


async def test_an_old_layout_member_is_foreign(tmp_path, database, run_dirs):
    """`submodule update --init` put its gitdir under the root's worktree
    gitdir, which a sandboxed worker writes; no connected repository knows it."""
    wt, sub, expected = await _member_checkout(database, run_dirs, tmp_path, connected=False)
    assert (wt / _REL / ".git").is_file()
    assert expected == {_REL: None}
    assert sandbox.foreign_members(wt, expected) == [_REL]


async def test_a_repository_nested_inside_a_member_is_planted(tmp_path, database, run_dirs):
    wt, _sub, _ = await _member_checkout(database, run_dirs, tmp_path)
    _nested(wt / _REL, "vendor/x")
    assert sandbox.planted_repos(wt / _REL, git(wt, "rev-parse", f"HEAD:{_REL}")) == ["vendor/x"]


async def test_a_connected_path_that_is_only_a_directory_in_another_repository_has_no_gitdirs(
    tmp_path, database, run_dirs
):
    """git in an empty directory answers for the repository around it; that
    repository is not the member's, so it is no trusted gitdir for one."""
    wt, sub, _ = await _member_checkout(database, run_dirs, tmp_path)
    (sub / "empty").mkdir()
    assert sandbox.member_gitdirs(sub / "empty", wt, _REL) is None
    assert sandbox.member_gitdirs(tmp_path / "gone", wt, _REL) is None


@pytest.mark.parametrize(("swapped", "found"), [("gitfile", "own"), ("member", None)])
async def test_a_member_symlinked_into_another_worktree_never_yields_that_ones_admin_dir(
    tmp_path, database, run_dirs, swapped, found
):
    """Resolving the member path would follow the worker's symlink into
    another checkout of the same repository and hand back that one's admin
    dir, to be mounted read-write. Only the worktree's own path is resolved,
    and a member reached through a symlink has no gitdirs at all."""
    wt, sub, expected = await _member_checkout(database, run_dirs, tmp_path)
    victim = tmp_path / "victim"
    git(sub, "worktree", "add", "-q", "-b", "kraft/victim", str(victim))
    link = wt / _REL / ".git" if swapped == "gitfile" else wt / _REL
    if swapped == "gitfile":
        link.unlink()
    else:
        (wt / _REL).rename(tmp_path / "member-aside")
    link.symlink_to(victim / ".git" if swapped == "gitfile" else victim)

    got = sandbox.member_gitdirs(sub, wt, _REL)
    assert got == (expected[_REL] if found == "own" else None)
