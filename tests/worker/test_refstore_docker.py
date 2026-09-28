"""The ref store against a real container runtime, docker and podman: the one
proof that the mount layout `docker_argv` builds is what git inside a
container actually sees, and that what it writes stays the operator's."""

import os
import subprocess
from pathlib import Path

import pytest

from kraft.paths import default_templates_dir
from kraft.worker import refstore
from kraft.worker.backends import docker

BRANCH = "kraft/item-1"
IMAGE = "kraft-test-git:latest"


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
    """`alpine/git` with its `git` entrypoint cleared, so a command runs as
    itself, built with the runtime under test and made this machine's
    runtime through `sandbox.yaml`, rootless or not as it really is. Skips
    where the runtime cannot build it, unless KRAFT_E2E_REQUIRE names it."""
    cli = request.param
    context = tmp_path / "image"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM docker.io/alpine/git:latest\nENTRYPOINT []\n")
    built = subprocess.run(
        [cli, "build", "-q", "-t", IMAGE, str(context)], capture_output=True, text=True
    )
    if built.returncode != 0:
        why = f"e2e: {cli} could not build {IMAGE}: {built.stderr.strip()}"
        # Where the runtime is required, an unreachable daemon or registry is
        # a failure, never a quiet skip past KRAFT_E2E_REQUIRE.
        if cli in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
            pytest.fail(why)
        pytest.skip(why)
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "sandbox.yaml").write_text(f"cli: {cli}\n")
    monkeypatch.setattr(docker, "_RUNTIME", docker.detect_runtime())
    return IMAGE


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
        refstore=store,
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
