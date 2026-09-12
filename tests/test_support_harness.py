"""tests/support/harness.py's own git plumbing, not the fixtures it builds."""

from __future__ import annotations

from pathlib import Path

from support.harness import _git


def test_git_commits_with_no_ambient_identity(tmp_path, monkeypatch):
    """Kraft-f5it: test_resume_marks_needs_human_on_a_rebase_conflict failed
    with `git commit` exiting 128 on a clean checkout. `_isolated_kraft_home`
    (tests/conftest.py) already points HOME at a directory with no
    .gitconfig for every test in the suite; this drives `_git` directly, with
    none of `make_repo`'s own `git config user.email/name` calls, to prove
    `_git` no longer depends on any config existing anywhere.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    # Block git's own username@hostname auto-detect so this test actually
    # pins spec section 6's "no ambient identity" condition, instead of
    # passing on hosts where the fallback identity resolves on its own.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "no ambient identity")  # must not exit 128
    log = Path.read_text(repo / ".git" / "HEAD")
    assert log  # the commit landed; a 128 exit would have raised in _git first
