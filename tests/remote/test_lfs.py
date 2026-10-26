"""LFS pointers and the object store, read with plain git and no git-lfs."""

import hashlib
from pathlib import Path

import pytest
from support.harness import commit_all, git, make_repo, write
from support.remote import lfs_pointer

from kraft.remote import lfs

OID = "a" * 64
V1 = "version https://git-lfs.github.com/spec/v1"


@pytest.mark.parametrize(
    ("data", "want"),
    [
        pytest.param(
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{OID}\nsize 12\n".encode(),
            (OID, 12),
            id="valid",
        ),
        pytest.param(
            f"version https://x/v2\noid sha256:{OID}\nsize 12\n".encode(), None, id="wrong-version"
        ),
        pytest.param(
            b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n",
            None,
            id="short-oid",
        ),
        pytest.param(
            (V1 + f"\noid sha256:{OID.upper()}\nsize 12\n").encode(),
            None,
            id="upper-hex",
        ),
        pytest.param(
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{OID}\n".encode(),
            None,
            id="no-size",
        ),
        pytest.param(b"\xff\xfe\x00\x01", None, id="binary"),
        pytest.param(
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{OID}\nsize 12\n".encode()
            + b"x" * 1024,
            None,
            id="over-1024-bytes",
        ),
    ],
)
def test_parse_pointer(data, want):
    assert lfs.parse_pointer(data) == want


def test_tree_pointers_finds_pointers_and_skips_ordinary_files(tmp_path):
    repo = make_repo(tmp_path)
    oid1, p1 = lfs_pointer(b"one")
    oid2, p2 = lfs_pointer(b"hello")
    write(repo, "a.bin", p1)
    write(repo, "sub/b.bin", p2)
    write(repo, "note.txt", "just a small text file\n")
    commit_all(repo)
    assert lfs.tree_pointers(repo, "HEAD") == {oid1: 3, oid2: 5}


def test_new_pointers_lists_only_what_the_new_commits_add(tmp_path):
    repo = make_repo(tmp_path)
    oid1, p1 = lfs_pointer(b"one")
    oid2, p2 = lfs_pointer(b"two!")
    write(repo, "a.bin", p1)
    first = commit_all(repo)
    write(repo, "b.bin", p2)
    second = commit_all(repo)
    assert lfs.new_pointers(repo, second, [first]) == {oid2: 4}
    assert lfs.new_pointers(repo, second, [first, "f" * 40]) == {oid2: 4}


def _src(tmp_path, body: bytes, name="src") -> tuple[str, Path]:
    path = tmp_path / name
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), path


@pytest.mark.parametrize(
    "case",
    ["ok", "wrong-hash", "wrong-size", "bad-oid", "one-bad-of-two"],
)
def test_take(tmp_path, case):
    store = tmp_path / "store"
    oid, src = _src(tmp_path, b"payload")
    objects = {oid: (src, 7)}
    if case == "wrong-hash":
        objects = {"b" * 64: (src, None)}
    elif case == "wrong-size":
        objects = {oid: (src, 8)}
    elif case == "bad-oid":
        objects = {"../x": (src, None)}
    elif case == "one-bad-of-two":
        _, forged = _src(tmp_path, b"other", "forged")
        objects = {oid: (src, None), "c" * 64: (forged, None)}
    if case == "ok":
        lfs.take(store, objects)
        assert lfs.object_path(store, oid).read_bytes() == b"payload"
        assert not src.exists()
        return
    with pytest.raises(lfs.LfsRefused) as raised:
        lfs.take(store, objects)
    if case == "wrong-hash":
        assert "b" * 64 in str(raised.value)
    assert lfs.held(store) == []
    assert src.exists()


def test_held_lists_stored_oids_only(tmp_path):
    store = tmp_path / "store"
    oid, src = _src(tmp_path, b"payload")
    lfs.take(store, {oid: (src, None)})
    write(store, "ab/cd/notanoid", "stray")
    assert lfs.held(store) == [oid]


def test_store_of_a_linked_worktree_is_the_common_dir(tmp_path):
    repo = make_repo(tmp_path)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "other", str(linked))
    assert lfs.store(linked) == lfs.store(repo)
    assert lfs.store(repo) == (repo / ".git" / "lfs" / "objects").resolve()
