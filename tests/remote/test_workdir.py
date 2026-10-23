"""The machine's copy of an item."""

from support.harness import commit_all, git, make_repo, write

from kraft.remote import bundle
from kraft.remote.workdir import Copy

IDENTITY = {
    "GIT_AUTHOR_NAME": "k",
    "GIT_AUTHOR_EMAIL": "k@x",
    "GIT_COMMITTER_NAME": "k",
    "GIT_COMMITTER_EMAIL": "k@x",
}


def _server(tmp_path):
    repo = make_repo(tmp_path, "server")
    git(repo, "checkout", "-q", "-b", "kraft/x")
    full = tmp_path / "full.bundle"
    assert bundle.make(repo, "kraft/x", [], full)
    return repo, git(repo, "rev-parse", "HEAD"), full


def test_a_copy_follows_the_branch_and_keeps_ignored_files_across_updates(tmp_path):
    server, head1, full = _server(tmp_path)
    copy = Copy(tmp_path / "m" / "items" / "w1" / "repo")
    copy.update(full, branch="kraft/x", head=head1)
    (copy.root / ".git" / "info" / "exclude").write_text("node_modules/\n")
    (copy.root / "node_modules").mkdir()
    (copy.root / "node_modules" / "keep").write_text("built once")
    write(server, "n.txt", "n")
    head2 = commit_all(server)
    inc = tmp_path / "inc.bundle"
    assert bundle.make(server, "kraft/x", bundle.usable(server, copy.commits()), inc)
    copy.update(inc, branch="kraft/x", head=head2)
    assert (copy.root / "n.txt").read_text() == "n"
    assert (copy.root / "node_modules" / "keep").read_text() == "built once"
    assert {head1, head2} <= set(copy.commits())


def test_a_step_that_changed_a_tracked_file_does_not_leak_into_the_next(tmp_path):
    server, head, full = _server(tmp_path)
    copy = Copy(tmp_path / "m" / "repo")
    copy.update(full, branch="kraft/x", head=head)
    tracked = next(p for p in copy.root.iterdir() if p.is_file() and p.name != ".git")
    original = tracked.read_text()
    tracked.write_text("scribbled")
    copy.update(None, branch="kraft/x", head=head)  # nothing new to fetch: the head is already here
    assert tracked.read_text() == original


async def test_the_sweep_commits_what_an_agent_left_and_the_bundle_carries_it(tmp_path):
    server, head, full = _server(tmp_path)
    copy = Copy(tmp_path / "m" / "repo")
    copy.update(full, branch="kraft/x", head=head)
    (copy.root / "left.txt").write_text("forgot to commit")
    assert await copy.sweep(branch="kraft/x", base=head, node_id="build", identity=IDENTITY)
    tip = copy.bundle_since("kraft/x", [head], tmp_path / "back.bundle")
    assert tip == git(copy.root, "rev-parse", "HEAD") != head
    assert copy.bundle_since("kraft/x", [tip], tmp_path / "again.bundle") is None  # nothing new
    assert not await copy.sweep(branch="kraft/x", base=head, node_id="build", identity=IDENTITY)
