"""The ref store against a real Docker daemon: the one proof that the mount
layout `docker_argv` builds is what git inside a container actually sees."""

import os
import subprocess

import pytest

from kraft.worker import refstore, sandbox

BRANCH = "kraft/item-1"
IMAGE = "kraft-test-git:latest"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(scope="module")
def git_image():
    """`alpine/git` with its `git` entrypoint cleared, so a command runs as
    itself. Skips where the daemon cannot be reached or the image pulled."""
    built = subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE, "-"],
        input="FROM alpine/git:latest\nENTRYPOINT []\n",
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        why = f"e2e: docker could not build {IMAGE}: {built.stderr.strip()}"
        # Where docker is required, an unreachable daemon or registry is a
        # failure, never a quiet skip past KRAFT_E2E_REQUIRE.
        if "docker" in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
            pytest.fail(why)
        pytest.skip(why)
    return IMAGE


@pytest.mark.e2e("docker")
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
    argv = sandbox.docker_argv(
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
    # Every mount point inside the store is Kraft's own, so the next session's
    # rebuild can remove it even where docker would have created it as root.
    refstore.prepare(tmp_path / "run", wt, BRANCH, session_id="s2")
