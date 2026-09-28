import os
import subprocess

from support.harness import make_repo

from kraft.worker import sandbox


def test_harden_host_git_env_pins_config_as_git_s_own_env_form():
    env = {}
    sandbox.harden_host_git_env(env)
    count = int(env["GIT_CONFIG_COUNT"])
    pinned = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)}
    assert pinned["core.hooksPath"] == os.devnull
    assert pinned["core.fsmonitor"] == ""


def _repo_with_a_planted_hook(tmp_path):
    """A repo whose config points `core.hooksPath` at a hook that writes a
    marker -- what a worker plants through any of the redirect files inside
    the gitdir it must be able to write."""
    repo = make_repo(tmp_path)
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.hooksPath", str(hooks)], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
    return repo, marker


def test_a_planted_hook_runs_without_the_hardened_env(tmp_path):
    """The other half of the test below: without it, this hook really does
    execute, so the assertion there is pinning the hardening and not a repo
    that could never have run a hook in the first place.

    Runs with `sandbox.unhardened_git_env()` rather than the inherited
    process env: a test session started under Kraft is itself a child of a
    process that already called `harden_host_git_env` (Kraft-rki), so the
    ambient env cannot be trusted to be unhardened."""
    repo, marker = _repo_with_a_planted_hook(tmp_path)
    subprocess.run(
        ["git", "commit", "-m", "x"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=sandbox.unhardened_git_env(),
    )
    assert marker.exists()


def test_hardened_env_stops_a_planted_hook_from_running(tmp_path):
    """Kraft-rki, the load-bearing half: a worker's worktree gitdir is
    writable by construction, so the guarantee cannot come from shadowing
    whichever file it plants `core.hooksPath` in. It comes from here -- git's
    own `GIT_CONFIG_COUNT` env form carries `-c` precedence, so it beats the
    repo and worktree config files that hook was configured in."""
    repo, marker = _repo_with_a_planted_hook(tmp_path)
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    subprocess.run(
        ["git", "commit", "-m", "x"], cwd=repo, check=True, capture_output=True, text=True, env=env
    )
    assert not marker.exists()


def test_hardened_env_still_lets_git_diff_run(tmp_path):
    """`diff.external` looks like it belongs in the pinned set and does not:
    git reads an empty value as the empty command and dies on every diff.
    This is the check that catches putting it back."""
    repo, _ = _repo_with_a_planted_hook(tmp_path)
    env = dict(os.environ)
    sandbox.harden_host_git_env(env)
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.stdout.split() == ["f.txt"]


def test_git_identity_comes_from_the_repository_when_the_daemon_has_none(
    repo, tmp_path, monkeypatch
):
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    subprocess.run(["git", "config", "user.name", "Op"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "op@x"], cwd=repo, check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "b", str(wt)], cwd=repo, check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Daemon")
    assert sandbox.git_identity(wt) == {
        "GIT_AUTHOR_NAME": "Daemon",
        "GIT_AUTHOR_EMAIL": "op@x",
        "GIT_COMMITTER_NAME": "Op",
        "GIT_COMMITTER_EMAIL": "op@x",
    }
