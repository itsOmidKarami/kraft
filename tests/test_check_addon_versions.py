"""`dev/check_addon_versions.py`: an add-on of Kraft's own collection that
changed has a new `version`, or an instance that already took the old one
never sees the change."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from support.addons import AGENT, make_collection, publish
from support.harness import commit_all, git, write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_addon_versions.py"
RELEASE = {"release": {"library": {"tasks": {"base": AGENT}}}}
MORE = {"library": {"tasks": {"base": AGENT, "more": AGENT}}}


@pytest.fixture(scope="module")
def cv():
    spec = importlib.util.spec_from_file_location("_dev_check_addon_versions", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("change", "found"),
    [
        ({}, []),
        (MORE, ["release"]),
        ({**MORE, "version": "1.1.0"}, []),
        ({**MORE, "version": "0.9.0"}, ["release"]),
        ({"files": {"README.md": "notes\n"}}, []),
    ],
    ids=[
        "unchanged",
        "changed-same-version",
        "changed-new-version",
        "changed-lower-version",
        "file-kraft-never-reads",
    ],
)
def test_a_changed_addon_needs_a_new_version(cv, tmp_path, change, found):
    repo = make_collection(tmp_path, RELEASE)
    base = git(repo, "rev-parse", "HEAD")
    if change:
        publish(repo, "release", **change)

    assert [line.split(":")[0] for line in cv.problems(repo, base)] == found


@pytest.mark.parametrize("before", ["absent", "no-manifest"])
def test_an_addon_the_base_lacks_is_new(cv, tmp_path, before):
    """`config/` was a directory long before it was the add-on kraft-default."""
    repo = make_collection(tmp_path, RELEASE)
    if before == "no-manifest":
        write(repo, "addons/tools/library.yaml", "tasks: {}\n")
        commit_all(repo, "a directory that is no add-on yet")
    base = git(repo, "rev-parse", "HEAD")
    publish(repo, "tools", library={"tasks": {"base": AGENT}})
    collection = repo / ".kraft/collection.json"
    collection.write_text(
        collection.read_text().replace(
            '"addons": [', '"addons": [{"name": "tools", "source": "./addons/tools"}, '
        )
    )

    assert cv.problems(repo, base) == []


def test_the_base_is_the_merge_base_or_the_commit_before(cv, tmp_path):
    repo = make_collection(tmp_path, RELEASE)
    first = git(repo, "rev-parse", "HEAD")
    publish(repo, "release", **MORE)
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "upstream")

    # On the upstream branch itself (a push to main): the commit before.
    assert cv.base_commit(repo, "upstream") == first
    git(repo, "checkout", "-q", "-b", "feature")
    publish(repo, "release", version="1.1.0")
    # A branch off it: where it left.
    assert cv.base_commit(repo, "upstream") == second


def test_a_named_base_must_be_a_commit_here(cv, tmp_path):
    repo = make_collection(tmp_path, RELEASE)
    head = git(repo, "rev-parse", "HEAD")

    assert cv.resolve_base(repo, head) == head
    with pytest.raises(SystemExit, match="is not a commit"):
        cv.resolve_base(repo, "deadbeef")
    # A push that created the branch has no `before`: the default base, here none.
    assert cv.resolve_base(repo, "0" * 40) is None
