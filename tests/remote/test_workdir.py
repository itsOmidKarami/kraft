"""The machine's copy of an item."""

import hashlib
import shutil
import subprocess

import pytest
from support.harness import commit_all, git, make_repo, write

from kraft.remote import bundle, lfs
from kraft.remote.workdir import Copy
from kraft.worker import sandbox

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
    (copy.bare / "info" / "exclude").write_text("node_modules/\n")
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


def test_the_copy_is_a_linked_worktree_so_ref_stores_apply(tmp_path):
    _, head, full = _server(tmp_path)
    copy = Copy(tmp_path / "m" / "items" / "w1" / "repo")
    copy.update(full, branch="kraft/x", head=head)
    common, _ = sandbox.linked_gitdirs(copy.root)
    assert common == copy.bare.resolve()
    assert git(copy.root, "rev-parse", "--abbrev-ref", "HEAD") == "kraft/x"


def test_an_old_plain_clone_is_rebuilt_as_a_worktree(tmp_path):
    server, head, full = _server(tmp_path)
    root = tmp_path / "m" / "items" / "w1" / "repo"
    subprocess.run(["git", "clone", "-q", str(server), str(root)], check=True)
    assert sandbox.linked_gitdirs(root) is None
    copy = Copy(root)
    copy.update(full, branch="kraft/x", head=head)
    assert sandbox.linked_gitdirs(root) is not None
    assert head in copy.commits()


def test_a_copy_marked_for_rebuild_is_replaced_not_checked_out_over(tmp_path):
    server, head, full = _server(tmp_path)
    copy = Copy(tmp_path / "m" / "repo")
    copy.update(full, branch="kraft/x", head=head)
    git(copy.root, "init", "-q", "planted")  # a repository the sandboxed worker made
    assert copy.planted(head) == ["planted"]
    copy.rebuild_on_update()
    copy.update(None, branch="kraft/x", head=head)
    assert not (copy.root / "planted").exists() and copy.planted(head) == []
    assert not (copy.bare / "kraft-rebuild").exists() and copy._is_worktree()


needs_git_lfs = pytest.mark.skipif(not shutil.which("git-lfs"), reason="git-lfs not installed")
ATTRS = "*.bin filter=lfs diff=lfs merge=lfs -text\n"


def _pointer_text(body: bytes) -> tuple[str, str]:
    oid = hashlib.sha256(body).hexdigest()
    return oid, f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(body)}\n"


def _lfs_server(tmp_path, body=b"real content"):
    repo = make_repo(tmp_path, "server")
    git(repo, "checkout", "-q", "-b", "kraft/x")
    oid, pointer = _pointer_text(body)
    write(repo, ".gitattributes", ATTRS)
    write(repo, "a.bin", pointer)
    head = commit_all(repo)
    full = tmp_path / "full.bundle"
    assert bundle.make(repo, "kraft/x", [], full)
    return oid, head, full


@needs_git_lfs
@pytest.mark.parametrize("held", [True, False], ids=["held", "missing"])
def test_a_copy_of_an_lfs_repo_checks_out_held_objects_and_leaves_missing_ones_as_pointers(
    tmp_path, held
):
    body = b"real content"
    oid, head, full = _lfs_server(tmp_path, body)
    copy = Copy(tmp_path / "m" / "repo")
    if held:
        sent = tmp_path / "sent"
        sent.write_bytes(body)
        copy.take_lfs({oid: sent})
    copy.update(full, branch="kraft/x", head=head)
    text = (copy.root / "a.bin").read_bytes()
    assert (text == body) if held else (lfs.parse_pointer(text) == (oid, len(body)))


def test_update_configures_lfs_and_checks_out_without_smudging(tmp_path, monkeypatch):
    oid, head, full = _lfs_server(tmp_path)
    calls = []
    monkeypatch.setattr(lfs, "available", lambda: True)
    monkeypatch.setattr(lfs, "configure", lambda bare: calls.append("configure"))
    monkeypatch.setattr(lfs, "checkout", lambda root: calls.append("checkout"))
    real_run = Copy._run
    checkouts = []

    def spy(cwd, *args, **kw):
        if args[0] in ("checkout", "worktree") and args[1:2] != ("prune",):
            checkouts.append((kw.get("env") or {}).get("GIT_LFS_SKIP_SMUDGE"))
        return real_run(cwd, *args, **kw)

    monkeypatch.setattr(Copy, "_run", staticmethod(spy))
    copy = Copy(tmp_path / "m" / "repo")
    copy.update(full, branch="kraft/x", head=head)  # a new copy: worktree add
    copy.update(None, branch="kraft/x", head=head)  # an existing one: checkout
    assert calls == ["configure", "checkout", "configure", "checkout"]
    assert checkouts == ["1", "1"]


@needs_git_lfs
def test_an_lfs_copy_cleans_a_new_large_file_into_a_pointer(tmp_path):
    oid, head, full = _lfs_server(tmp_path)
    copy = Copy(tmp_path / "m" / "repo")
    copy.update(full, branch="kraft/x", head=head)
    (copy.root / "b.bin").write_bytes(b"x" * 5000)
    copy._git("add", "b.bin")
    copy._git("commit", "-qm", "b")
    blob = copy._git("show", "HEAD:b.bin").stdout.encode()
    new_oid = hashlib.sha256(b"x" * 5000).hexdigest()
    assert lfs.parse_pointer(blob) == (new_oid, 5000)
    assert lfs.object_path(lfs.store(copy.bare), new_oid).is_file()
