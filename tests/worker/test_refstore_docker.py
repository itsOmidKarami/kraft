"""The ref store against a real container runtime, docker and podman: the one
proof that the mount layout `docker_argv` builds is what git inside a
container actually sees, and that what it writes stays the operator's."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from support.harness import entry_of
from support.sandbox_image import build_git_image
from support.workspace import nested_repositories, repositories, workspace_item

from kraft import builtins
from kraft.adapters import forge
from kraft.executor import stops
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy, TemplatePolicyOverride
from kraft.worker import refstore, sandbox
from kraft.worker.backends import docker

from .test_host_git_trust import PROGRAM_KEYS

BRANCH = "kraft/item-1"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def git_image(request, tmp_path, monkeypatch):
    return build_git_image(request.param, tmp_path, monkeypatch)


def test_a_sandboxed_worker_moves_only_its_own_branch(repo, tmp_path, git_image):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    main = _git(repo, "rev-parse", "main")
    _git(repo, "branch", "doomed")
    store = refstore.prepare(tmp_path / "run", wt, BRANCH, session_id="s1")
    script = (
        "set -e; echo work > work.txt; git add work.txt; git commit -qm work;"
        " git update-ref refs/heads/main HEAD;"
        # Deleting a ref rewrites packed-refs, which lives in the store.
        " git branch -D doomed"
    )
    argv = docker.docker_argv(
        ["sh", "-c", script],
        wt,
        {"kind": "docker", "image": git_image},
        None,
        env={
            "GIT_AUTHOR_NAME": "w",
            "GIT_AUTHOR_EMAIL": "w@x",
            "GIT_COMMITTER_NAME": "w",
            "GIT_COMMITTER_EMAIL": "w@x",
        },
        refstores=(store,),
    )
    ran = subprocess.run(argv, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert refstore.sync(store, "s1") is None
    assert _git(repo, "rev-parse", "main") == main
    assert _git(repo, "log", "-1", "--format=%s", BRANCH) == "work"
    assert _git(repo, "rev-parse", "--verify", "doomed")
    # A rootless runtime maps container uids into a subordinate range: what
    # the worker wrote must still be the operator's to edit and remove.
    assert (wt / "work.txt").stat().st_uid == os.getuid()
    # Every mount point inside the store is Kraft's own, so the next session's
    # rebuild can remove it even where docker would have created it as root.
    refstore.prepare(tmp_path / "run", wt, BRANCH, session_id="s2")


_REL = "repos/pkg"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "w",
    "GIT_AUTHOR_EMAIL": "w@x",
    "GIT_COMMITTER_NAME": "w",
    "GIT_COMMITTER_EMAIL": "w@x",
}


def _bare_origin(repo: Path, origin: Path) -> None:
    _git(repo.parent, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "config", "remote.origin.url", str(origin))
    _git(repo, "push", "-q", "origin", "main")


def _parents(rel: Path) -> list[Path]:
    """`repos/pkg` -> `repos`, `repos/pkg`."""
    return [Path(*rel.parts[: i + 1]) for i in range(len(rel.parts))]


def _script(
    root_wt: Path,
    member: Path,
    plant: list[Path],
    hooks: list[Path],
    read_only: list[Path],
    config: dict[str, str],
    marker: Path,
) -> str:
    """The worker's session: a commit in the member and one in the root taking
    its gitlink, `main` moved in both, then every program-valued key planted
    wherever the container lets it write, then a write and a rename tried on
    each path that must stay put -- `WROTE`/`RENAMED <path>` for any that
    took -- and every directory on the way to the member renamed aside, the
    way a worker would swap the member for one of its own."""
    q = shlex.quote
    lines = [
        "set -e",
        f"cd {q(str(member))}",
        "echo work > member.txt",
        "printf '* filter=evil diff=evil\\n' > .gitattributes",
        "git add -A",
        "git commit -qm 'member work'",
        "git update-ref refs/heads/main HEAD",
        f"cd {q(str(root_wt))}",
        "echo work > root.txt",
        "git add -A",
        "git commit -qm 'root work'",
        "git update-ref refs/heads/main HEAD",
        "set +e",
    ]
    for file in plant:
        lines.append(f"mkdir -p {q(str(file.parent))} 2>/dev/null")
        lines += [
            f"git config --file {q(str(file))} {q(k)} {q(v)} 2>/dev/null" for k, v in config.items()
        ]
    hook = f"#!/bin/sh\ntouch {marker}\n"
    for directory in hooks:
        lines.append(f"mkdir -p {q(str(directory))} 2>/dev/null")
        for name in ("pre-push", "pre-commit", "post-checkout", "reference-transaction"):
            path = q(str(directory / name))
            lines.append(f"printf {q(hook)} > {path} 2>/dev/null && chmod +x {path}")
    lines += [
        f"printf '' >> {q(str(path))} 2>/dev/null && echo WROTE {q(str(path))}"
        for path in read_only
    ]
    lines += [
        f"mv {q(str(path))} {q(str(path))}.x 2>/dev/null && echo RENAMED {q(str(path))}"
        for path in [*read_only, *[root_wt / p for p in _parents(member.relative_to(root_wt))]]
    ]
    lines.append(
        f"printf 'gitdir: /tmp/x\\n' > {q(str(member / '.git'))} 2>/dev/null && echo SWAPPED"
    )
    lines.append("true")
    return "\n".join(lines)


@pytest.mark.parametrize("nested", [False, True], ids=["own-repository", "inside-the-roots-gitdir"])
async def test_a_sandboxed_workspace_worker_commits_in_root_and_member(
    database, run_dirs, tmp_path, git_image, nested
):
    """Kraft-ju36l end to end in a real container: a sandboxed worker commits
    in the member and in the root, and every repository's item branch reaches
    its origin while no `main` moves. Whatever it plants in the gitdirs it can
    write -- every program-valued key, and hooks -- no host git Kraft runs
    afterwards executes; and the files host git trusts for the member, its
    `.git` and its admin dir's `commondir`, `gitdir` and `config.worktree`,
    it cannot write at all (J6), so nothing swaps between the drift check and
    host git."""
    policy = InstancePolicy.from_input(InstancePolicyInput()).apply_template_override(
        TemplatePolicyOverride(sandbox=SandboxPolicy(kind="docker", image=git_image))
    )
    task = {"id": "t", "kind": "subprocess", "command": "true"}
    row, _, wt = await workspace_item(
        database,
        run_dirs,
        tmp_path,
        [task],
        nested=nested,
        effective_policy=policy,
        repository_policies={"pkg": policy},
    )
    root = Path(row["repo"])
    m = root / _REL if nested else tmp_path / "pkg-connected"
    connected = (
        nested_repositories(root, {"pkg": _REL}) if nested else repositories(tmp_path, "pkg")
    )
    launch = LaunchContext(repo_entry=entry_of({"setup_command": ""}), repositories=connected)
    _bare_origin(root, tmp_path / "root-origin.git")
    _bare_origin(m, tmp_path / "pkg-origin.git")
    wt, member = wt.resolve(), wt.resolve() / _REL
    branch = _git(wt, "branch", "--show-current")
    mains = {r: _git(r, "rev-parse", "main") for r in (root, m)}
    checkout = stops.sandbox_checkout(row, launch, wt)
    assert None not in checkout.members.values()
    stores = refstore.prepare_stores(
        run_dirs.base, wt, branch, members=checkout.members, session_id="s1", work_item_id=row["id"]
    )
    root_admin, member_admin = (s.worktree_gitdir for s in stores)
    marker, planted = tmp_path / "PWNED", tmp_path / "planted"
    planted.mkdir()
    config = {k: v for build, _ in PROGRAM_KEYS.values() for k, v in build(marker, planted).items()}
    old_modules = root_admin / "modules" / _REL
    read_only = [
        path
        for git_file, admin in ((wt / ".git", root_admin), (member / ".git", member_admin))
        for path in (git_file, admin / "commondir", admin / "gitdir", admin / "config.worktree")
    ]
    script = _script(
        wt,
        member,
        plant=[root_admin / "config", member_admin / "config", old_modules / "config"],
        hooks=[root_admin / "hooks", member_admin / "hooks", old_modules / "hooks"],
        read_only=read_only,
        config=config,
        marker=marker,
    )
    argv = docker.docker_argv(
        ["sh", "-c", script],
        wt,
        {"kind": "docker", "image": git_image},
        None,
        env=_IDENTITY,
        refstores=stores,
    )

    ran = subprocess.run(argv, capture_output=True, text=True)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.split() == [], "the container wrote a path it must only read"
    assert refstore.sync_all(stores, "s1") == []
    sandbox.harden_host_git_env()
    stops.refuse_planted_repos(row, launch, wt)
    identity = builtins.item_identity(database, row["id"])
    for repo, mounts in ((member, []), (wt, [_REL])):
        await forge.commit_stragglers(
            repo, branch=branch, base="main", message="wip", mounts=mounts, identity=identity
        )
        await forge.assert_clean(repo, "main")
        await forge.git.push(repo, branch)
    for origin, subject in (("root-origin.git", "root work"), ("pkg-origin.git", "member work")):
        assert subject in _git(tmp_path / origin, "log", "--format=%s", branch).splitlines()
    assert {r: _git(r, "rev-parse", "main") for r in (root, m)} == mains
    assert not marker.exists()
