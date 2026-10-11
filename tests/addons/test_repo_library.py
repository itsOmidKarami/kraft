"""A connected repository's `.kraft/` folder, read from origin's default branch."""

import json
import subprocess

import pytest
import yaml
from support.addons import AGENT, repo_library_files, with_origin
from support.harness import commit_all, git, make_repo, write

from kraft.addons import fetch, manifest, repo_library

_library = repo_library_files


@pytest.fixture
def app(tmp_path):
    repo = make_repo(tmp_path, "app")
    with_origin(repo)
    return repo


def test_the_commit_comes_from_the_remote_not_the_checkout(app):
    pushed = _library(app)
    # A commit the remote never saw, and a remote-tracking ref moved onto it.
    write(app, ".kraft/library.yaml", yaml.safe_dump({"tasks": {"base": {**AGENT, "prompt": "x"}}}))
    local = commit_all(app, "not pushed")
    branch = git(app, "rev-parse", "--abbrev-ref", "HEAD")
    git(app, "update-ref", f"refs/remotes/origin/{branch}", local)

    found = repo_library.remote(app)

    assert (found.commit, found.branch) == (pushed, branch)
    assert "@" not in found.url


def test_extract_gives_the_layout_files_and_a_derived_manifest(app):
    head = _library(
        app,
        library={"tasks": {"base": {**AGENT, "profile": "strong"}}},
        skills={"notes": "# notes\n"},
        extra={".kraft/collection.json": "{}", ".kraft/README.md": "hi"},
    )

    extracted = repo_library.extract(app, head)

    assert sorted(extracted.files) == [
        ".kraft/addon.json",
        "chains/ship.yaml",
        "library.yaml",
        "skills/notes/SKILL.md",
    ]
    declared = json.loads(extracted.files[manifest.ADDON_JSON][1])
    assert declared["name"] == "library"
    assert declared["requires"]["harnesses"] == ["codex"]
    assert declared["requires"]["profiles"] == ["strong"]
    assert manifest.addon(declared, manifest.ADDON_JSON).name == "library"
    assert extracted.tree == git(app, "rev-parse", f"{head}:.kraft")


def test_the_same_files_give_the_same_digest(app):
    first = repo_library.extract(app, _library(app))
    write(app, "README.md", "unrelated\n")
    commit_all(app, "elsewhere")
    git(app, "push", "-q", "origin", "HEAD")

    again = repo_library.extract(app, repo_library.remote(app).commit)

    assert fetch.digest(again.files) == fetch.digest(first.files)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no-kraft-folder"),
        pytest.param({".kraft/collection.json": "{}"}, id="only-a-collection"),
    ],
)
def test_a_repo_with_no_layout_file_has_no_repo_library(app, files):
    for rel, text in files.items():
        write(app, rel, text)
    write(app, "x", "x")
    head = commit_all(app, "no library")
    git(app, "push", "-q", "origin", "HEAD")

    assert repo_library.extract(app, head) is None


def test_a_commit_the_checkout_lacks_is_fetched(app, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", git(app, "remote", "get-url", "origin"), str(other))
    head = _library(app)
    lacks = subprocess.run(["git", "-C", str(other), "cat-file", "-e", head], check=False)
    assert lacks.returncode != 0

    assert repo_library.extract(other, head) is not None


def test_a_repo_with_no_origin_is_refused_by_name(tmp_path):
    repo = make_repo(tmp_path, "lonely")

    with pytest.raises(fetch.AddonRefused, match="no remote named origin"):
        repo_library.remote(repo)


def test_an_origin_that_does_not_answer_fails_as_network(app):
    git(app, "remote", "set-url", "origin", (app.parent / "gone.git").as_uri())

    with pytest.raises(fetch.FetchError) as caught:
        repo_library.remote(app)
    assert caught.value.kind == "network"


def test_missing_blobs_are_refused_with_the_commit_named(app, monkeypatch):
    head = _library(app)

    def gone(*_a, **_k):
        raise IndexError("missing")

    monkeypatch.setattr(fetch, "_sizes", gone)

    with pytest.raises(fetch.AddonRefused, match=head[:12]):
        repo_library.extract(app, head)
