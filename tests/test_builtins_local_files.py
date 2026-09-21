"""`local_files`: carrying ignored, uncommitted files (a `.python-version`
pin, say) from the developer's checkout into a new worktree (Kraft-gxcmy)."""

import subprocess

from support import worktree as wtree
from support.harness import _git

from kraft import builtins as kraft_builtins
from kraft.config import git_read


def _commit(cwd, name, text, message):
    """Write `name` under `cwd` and commit everything; returns the new HEAD."""
    (cwd / name).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-m", message)
    return git_read(cwd, "rev-parse", "HEAD")


async def test_setup_runs_after_local_files_are_carried(database, run_dirs, repo):
    """Kraft-gxcmy: uv picks an interpreter when it runs, so a pin that lands
    after the toolchain is a pin that changed nothing."""
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    await wtree.make_item(database, repo)
    wt = await kraft_builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(repo),
        work_item_id="w1",
        repo_entry={
            "setup_command": "cp .python-version seen-by-setup.txt",
            "local_files": [".python-version"],
        },
    )
    assert (wt / "seen-by-setup.txt").read_text().strip() == "3.11"


def _linked_worktree(repo, tmp_path, name="wt"):
    wt = tmp_path / name
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt), "-b", name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return wt


def test_carry_local_files_copies_an_ignored_untracked_file(tmp_path, repo):
    """The whole point of Kraft-gxcmy: the pin exists in the developer's
    checkout and nowhere in the worktree, because it was never committed."""
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == [".python-version"]
    assert refused == []
    assert (wt / ".python-version").read_text() == "3.11\n"
    # and invisible to git, so `open_mr`'s dirty-worktree guard never sees it
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True
    )
    assert status.stdout == ""


def test_carry_local_files_refuses_a_file_the_worktree_would_not_ignore(tmp_path, repo):
    """Kraft cannot hold an unignored file out of a commit, so it declines to
    create one. A refusal is reported, never raised."""
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == [".python-version"]
    assert not (wt / ".python-version").exists()


def test_carry_local_files_skips_a_symlinked_destination(tmp_path, repo):
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n")
    (wt / ".python-version").symlink_to(outside)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []
    assert outside.read_text() == "untouched\n"


def test_carry_local_files_leaves_an_existing_destination_alone(tmp_path, repo):
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    (wt / ".python-version").write_text("3.12\n")

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []
    assert (wt / ".python-version").read_text() == "3.12\n"


def test_carry_local_files_ignores_a_source_that_is_not_there(tmp_path, repo):
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert carried == []
    assert refused == []


def test_carry_local_files_refuses_a_directory_entry_missing_its_trailing_slash(tmp_path, repo):
    """`config.py` only rejects a directory entry that ends in `/`, so a plain
    `.venv` in `local_files` passes validation and reaches here. It is not a
    file, so it always fails `src.is_file()` -- the same branch a genuinely
    absent source takes. Without this, a typo like this is carried nowhere,
    refused nowhere, and never shows up in the preparation report either
    (`_uncarried_local_files`'s `"/" not in n` filter drops the `--directory`
    listing's `.venv/` entry) -- zero feedback for a plausible mistake."""
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    wt = _linked_worktree(repo, tmp_path)

    carried, refused = kraft_builtins._carry_local_files(repo, wt, [".venv"])

    assert carried == []
    assert refused == [".venv"]


async def test_ensure_worktree_without_local_files_is_unchanged(database, run_dirs, repo):
    """The feature is opt-in: an unconfigured repo must behave exactly as before."""
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    await wtree.make_item(database, repo, chain_template="default")
    worktree = await wtree.ensure(database, run_dirs, repo)
    assert not (worktree / ".python-version").exists()


def test_uncarried_local_files_names_root_files_missing_from_the_worktree(tmp_path, repo):
    """An unconfigured repo is the default, so the gap has to be visible
    without anyone having configured anything (Kraft-gxcmy)."""
    _commit(repo, ".gitignore", ".venv/\n.env\n", "ignore local state")
    (repo / ".python-version").write_text("3.11\n")  # untracked, not ignored
    (repo / ".env").write_text("TOKEN=x\n")  # untracked and ignored
    (repo / ".venv").mkdir()  # a directory: never reported
    (repo / ".venv" / "marker").write_text("x\n")
    wt = _linked_worktree(repo, tmp_path)

    missing = kraft_builtins._uncarried_local_files(repo, wt)

    assert missing == [".env", ".python-version"]


def test_uncarried_local_files_omits_what_was_carried(tmp_path, repo):
    _commit(repo, ".gitignore", ".python-version\n", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    wt = _linked_worktree(repo, tmp_path)
    kraft_builtins._carry_local_files(repo, wt, [".python-version"])

    assert kraft_builtins._uncarried_local_files(repo, wt) == []
