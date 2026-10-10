"""Fetching a collection into a bare mirror and extracting one plugin from it
as raw blobs, under the limits; the digest; the store."""

import pytest
from support.harness import commit_all, git, write
from support.plugins import AGENT, make_collection

from kraft.plugins import fetch

RELEASE = {"release": {"library": {"tasks": {"base": AGENT}}}}


@pytest.fixture
def collection(tmp_path):
    return make_collection(tmp_path, RELEASE)


@pytest.fixture
def plugins_dir(tmp_path):
    return tmp_path / "run" / "plugins"


def test_fetch_never_checks_out_or_runs_hooks(collection, plugins_dir, tmp_path):
    mirror, commit = fetch.fetch(plugins_dir, "acme", collection.as_uri(), None)
    assert commit == git(collection, "rev-parse", "HEAD")
    assert git(mirror, "rev-parse", "--is-bare-repository") == "true"
    assert not list(mirror.glob("plugins")) and not (mirror / ".kraft").exists()

    # A hook in the mirror would run on the next fetch's ref update.
    marker = tmp_path / "hook-ran"
    hook = write(mirror, "hooks/reference-transaction", f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    write(collection, "README.md", "more\n")
    commit_all(collection, "more")
    _, newer = fetch.fetch(plugins_dir, "acme", collection.as_uri(), None)
    assert newer != commit
    assert not marker.exists()


def test_a_rewritten_history_keeps_the_pinned_commit(collection, plugins_dir):
    """The author force-pushes; the commit an instance locked must still be
    readable from the mirror after a gc."""
    mirror, commit = fetch.fetch(plugins_dir, "acme", collection.as_uri(), None)
    fetch.pin(mirror, commit)
    write(collection, "README.md", "rewritten\n")
    git(collection, "add", "-A")
    git(collection, "commit", "-q", "--amend", "-m", "rewritten")
    fetch.fetch(plugins_dir, "acme", collection.as_uri(), None)
    git(mirror, "reflog", "expire", "--expire=now", "--all")
    git(mirror, "gc", "-q", "--prune=now")
    assert git(mirror, "cat-file", "-t", commit) == "commit"


def test_a_collection_re_added_from_another_source_gets_its_own_mirror(plugins_dir):
    a = fetch.mirror_path(plugins_dir, "acme", "https://github.com/acme/a.git")
    b = fetch.mirror_path(plugins_dir, "acme", "https://github.com/acme/b.git")
    assert a != b and a.parent == plugins_dir / "mirrors" and a.name.startswith("acme-")


def test_a_failed_fetch_is_classified_and_redacted(plugins_dir, tmp_path):
    with pytest.raises(fetch.FetchError) as exc:
        fetch.fetch(plugins_dir, "acme", (tmp_path / "nowhere").as_uri(), None)
    assert exc.value.kind == "network"
    assert fetch.redact("fatal: unable to access 'https://omid:s3cret@host/x.git/'") == (
        "fatal: unable to access 'https://host/x.git/'"
    )
