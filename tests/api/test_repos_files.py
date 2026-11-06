"""`GET /api/repos/files`, the attach picker's file list."""

from __future__ import annotations

import os
import shutil

import pytest
from support.harness import commit_all, connect_repo, make_repo, write

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


@pytest.mark.parametrize(
    ("name", "listed"),
    [
        pytest.param("docs/tracked.md", True, id="tracked"),
        pytest.param("notes/untracked.md", True, id="untracked"),
        pytest.param(".engineering/specs/ignored.md", True, id="ignored-engineering"),
        pytest.param("docs/superpowers/plans/ignored.md", True, id="ignored-superpowers"),
        pytest.param("docs/superpowers/specs/a b é.md", True, id="space-and-unicode"),
        pytest.param("node_modules/pkg/README.md", False, id="ignored-elsewhere"),
        pytest.param(".engineering/specs/notes.txt", False, id="not-markdown"),
        pytest.param(".engineering/specs/link.md", False, id="symlink-out"),
        pytest.param("docs/gone.md", False, id="tracked-but-deleted"),
        pytest.param("docs/linked/x.md", False, id="symlinked-directory"),
    ],
)
def test_repo_files_lists_the_markdown_a_picker_may_attach(
    client, tmp_path, templates_dir, name, listed
):
    repo = make_repo(tmp_path)
    with (repo / ".gitignore").open("a") as f:
        f.write(".engineering/\ndocs/superpowers/\nnode_modules/\n")
    write(repo, "docs/tracked.md", "# t\n")
    write(repo, "docs/gone.md", "# g\n")
    write(repo, "docs/linked/x.md", "# l\n")
    commit_all(repo)
    (repo / "docs/gone.md").unlink()
    # A tracked file whose directory has since become a link out of the repo.
    shutil.rmtree(repo / "docs/linked")
    os.symlink(write(tmp_path, "elsewhere/x.md", "secret\n").parent, repo / "docs/linked")
    if name.endswith("link.md"):
        outside = write(tmp_path, "outside.md", "secret\n")
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, repo / name)
    elif name not in ("docs/gone.md", "docs/tracked.md", "docs/linked/x.md"):
        write(repo, name, "# x\n")
    # `connect_repo` stores the resolved path; the request carries it as given.
    connect_repo(repo, templates_dir)

    r = client.get("/api/repos/files", params={"path": str(repo)})

    assert r.status_code == 200
    assert (name in [f["path"] for f in r.json()["files"]]) == listed


def test_repo_files_lists_newest_first(client, tmp_path, templates_dir):
    repo = make_repo(tmp_path)
    old = write(repo, "notes/old.md", "# old\n")
    write(repo, "notes/new.md", "# new\n")
    os.utime(old, (1, 1))
    connect_repo(repo, templates_dir)

    files = client.get("/api/repos/files", params={"path": str(repo)}).json()["files"]

    paths = [f["path"] for f in files]
    assert paths.index("notes/new.md") < paths.index("notes/old.md")


@pytest.mark.parametrize(
    ("kind", "status", "body"),
    [
        pytest.param("unconnected", 404, "is not connected", id="unconnected"),
        pytest.param("relative", 404, "not connected", id="relative"),
        pytest.param("not-git", 200, {"files": [], "truncated": False}, id="not-git"),
    ],
)
def test_repo_files_answers_only_for_a_connected_git_repo(
    client, tmp_path, templates_dir, kind, status, body
):
    if kind == "relative":
        path = "."
    else:
        path = str(tmp_path / kind)
        os.makedirs(path)
        if kind == "not-git":
            connect_repo(path, templates_dir)

    r = client.get("/api/repos/files", params={"path": path})

    assert r.status_code == status
    if isinstance(body, str):
        assert body in r.text
    else:
        assert r.json() == body
