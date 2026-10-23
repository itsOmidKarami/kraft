"""Git bundles between the server's worktree and a machine's copy."""

import subprocess

import pytest
from support.harness import commit_all, git, make_repo, write

from kraft.remote import bundle


def _branch(repo, name="kraft/x"):
    git(repo, "checkout", "-q", "-b", name)
    return name


def test_a_first_bundle_carries_the_whole_branch_and_a_later_one_only_what_is_new(tmp_path):
    repo = make_repo(tmp_path)
    b = _branch(repo)
    first = git(repo, "rev-parse", "HEAD")
    full = tmp_path / "full.bundle"
    assert bundle.make(repo, b, [], full)
    write(repo, "n.txt", "n")
    tip = commit_all(repo)
    inc = tmp_path / "inc.bundle"
    assert bundle.make(repo, b, [first], inc) and inc.stat().st_size < full.stat().st_size
    assert not bundle.make(repo, b, [tip], tmp_path / "none.bundle")  # nothing new: no empty bundle


def test_an_unknown_commit_the_machine_reports_is_not_an_exclusion(tmp_path):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    assert bundle.usable(repo, ["0" * 40, head, "not-a-sha"]) == [head]


def _returned(tmp_path, change):
    """A machine's clone that adds one commit (or whatever `change` does), as its bundle."""
    server = make_repo(tmp_path, "server")
    b = _branch(server)
    base = git(server, "rev-parse", "HEAD")
    machine = tmp_path / "machine"
    subprocess.run(["git", "clone", "-q", "-b", b, str(server), str(machine)], check=True)
    git(machine, "config", "user.email", "m@x")
    git(machine, "config", "user.name", "m")
    change(machine)
    out = tmp_path / "back.bundle"
    assert bundle.make(machine, b, [base], out)
    return server, b, base, out


def test_returned_commits_are_fetched_and_published_by_compare_and_swap(tmp_path):
    server, b, base, out = _returned(tmp_path, lambda m: (write(m, "new.txt", "x"), commit_all(m)))
    tip = bundle.fetch(server, out, branch=b, base=base, into_ref="refs/kraft/remote/w1/s1")
    got = bundle.publish(server, b, old=base, new=tip, key="w1/s1")
    assert got.ok and git(server, "rev-parse", f"refs/heads/{b}") == tip
    assert (server / "new.txt").read_text() == "x"  # the worktree followed the branch


def test_a_branch_that_moved_meanwhile_keeps_the_commits_unsynced_and_is_not_overwritten(tmp_path):
    server, b, base, out = _returned(tmp_path, lambda m: (write(m, "new.txt", "x"), commit_all(m)))
    write(server, "other.txt", "o")
    moved = commit_all(server)
    tip = bundle.fetch(server, out, branch=b, base=base, into_ref="refs/kraft/remote/w1/s1")
    got = bundle.publish(server, b, old=base, new=tip, key="w1/s1")
    assert not got.ok and got.ref.startswith("refs/kraft/unsynced/")
    assert git(server, "rev-parse", f"refs/heads/{b}") == moved
    assert git(server, "rev-parse", got.ref) == tip


def test_a_bundle_that_plants_a_repository_is_refused_and_leaves_the_branch_alone(tmp_path):
    def plant(m):
        git(m, "update-index", "--add", "--cacheinfo", "160000", "1" * 40, "evil")
        git(m, "commit", "-q", "-m", "gitlink")

    server, b, base, out = _returned(tmp_path, plant)
    before = git(server, "rev-parse", f"refs/heads/{b}")
    with pytest.raises(bundle.BundleRefused, match="evil"):
        bundle.fetch(server, out, branch=b, base=base, into_ref="refs/kraft/remote/w1/s1")
    assert git(server, "rev-parse", f"refs/heads/{b}") == before


def test_a_bundle_that_is_not_the_item_branch_or_does_not_continue_from_the_base_is_refused(
    tmp_path,
):
    server, b, base, out = _returned(tmp_path, lambda m: (write(m, "n", "n"), commit_all(m)))
    with pytest.raises(bundle.BundleRefused, match="do not continue"):
        bundle.fetch(server, out, branch=b, base="0" * 40, into_ref="refs/kraft/remote/w1/s1")
    garbage = tmp_path / "junk.bundle"
    garbage.write_bytes(b"not a bundle")
    with pytest.raises(bundle.BundleRefused):
        bundle.fetch(server, garbage, branch=b, base=base, into_ref="refs/kraft/remote/w1/s1")
