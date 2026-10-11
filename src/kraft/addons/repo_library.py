"""A connected repository's own library: the four add-on files, in `.kraft/`
on `origin`'s default branch, loaded under the repository's id.

A repo library is stored and loaded as an add-on. What an add-on's
`.kraft/addon.json` declares is derived here and written into the extracted
files, so the store, the digest, the review and the load-time checks read a
repo library as they read any add-on.

The commit to read is the one the remote names. The checkout lends its
credentials and its object store and is trusted for nothing else: none of its
branches, remote-tracking refs or files is read.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from kraft.addons import fetch, manifest, update
from kraft.config import GIT_READ_ENV, bounded_yaml
from kraft.worker.sandbox import harden_host_git_env

#: The name every repo library's derived manifest carries. Inside the files it
#: is the library's own qualifier (`library:base`), as an add-on's name is.
NAME = "library"
SOURCE = "./.kraft"
#: A manifest needs both. Neither is compared: a repo library changes when its
#: digest does, and `requires.kraft` is never checked for one.
VERSION = "0.0.0"
KRAFT = "2"
REMOTE_TIMEOUT_S = 60


@dataclass(frozen=True)
class Remote:
    """What `origin` answered: its URL (credentials removed), its default
    branch and that branch's commit."""

    url: str
    branch: str
    commit: str


def _git(checkout: Path, *args: str, timeout: int | None = None) -> str:
    """One git command in `checkout`, with its own config and credentials,
    that never prompts, never runs a hook and never fetches lazily."""
    env = {**os.environ, **GIT_READ_ENV}
    harden_host_git_env(env)
    command = ["git", "-C", str(checkout), "-c", "core.hooksPath=/dev/null", *args]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise fetch.FetchError("network", f"git timed out after {timeout}s") from exc
    if done.returncode != 0:
        said = fetch.redact(done.stderr.strip())
        raise fetch.FetchError("auth" if fetch._AUTH.search(said) else "network", said)
    return done.stdout


def remote(checkout: Path) -> Remote:
    """Ask `checkout`'s `origin` which commit its default branch is at."""
    try:
        url = _git(checkout, "remote", "get-url", "origin").strip()
    except fetch.FetchError as exc:
        raise fetch.AddonRefused(
            f"{checkout} has no remote named origin; a repo library is read from origin"
        ) from exc
    answer = _git(checkout, "ls-remote", "--symref", "origin", "HEAD", timeout=REMOTE_TIMEOUT_S)
    branch = commit = None
    for line in answer.splitlines():
        head, _, name = line.partition("\t")
        if name != "HEAD":
            continue
        if head.startswith("ref: "):
            branch = head.removeprefix("ref: ").removeprefix("refs/heads/")
        else:
            commit = head
    if not branch or not commit:
        raise fetch.FetchError("network", f"{fetch.redact(url)} did not name a default branch")
    return Remote(fetch.redact(url), branch, commit)


def derive_manifest(files: Mapping[str, tuple[str, bytes]]) -> dict:
    """The `.kraft/addon.json` a repo library would have written: every
    harness its tasks and fallbacks name, and every instance agent profile
    (a bare `profile:`) its tasks select. A file that does not parse adds
    nothing here; `update.problems` says why it is refused."""
    harnesses: set[str] = set()
    profiles: set[str] = set()
    for rel in sorted(files):
        if not (rel in ("library.yaml", "profiles.yaml") or rel.startswith("chains/")):
            continue
        try:
            data = bounded_yaml(files[rel][1].decode(), [20_000])
        except (ValueError, yaml.YAMLError):
            continue
        for _at, component in update._components(rel, data):
            harness = component.get("harness")
            if isinstance(harness, str):
                harnesses.add(harness)
            profile = component.get("profile")
            if isinstance(profile, str) and ":" not in profile:
                profiles.add(profile)
    return {
        "name": NAME,
        "version": VERSION,
        "requires": {
            "kraft": KRAFT,
            "harnesses": sorted(harnesses),
            "profiles": sorted(profiles),
        },
    }


def extract(checkout: Path, commit: str) -> fetch.Extracted | None:
    """`.kraft/` of `commit` as an add-on's files, with the derived manifest;
    None when the commit holds no layout file there. The commit is fetched
    from `origin` when the checkout lacks it."""
    try:
        _git(checkout, "cat-file", "-e", f"{commit}^{{commit}}")
    except fetch.FetchError:
        _git(checkout, "fetch", "--quiet", "--no-tags", "origin", commit, timeout=REMOTE_TIMEOUT_S)
    try:
        _git(checkout, "rev-parse", "--verify", "--quiet", f"{commit}:.kraft")
    except fetch.FetchError:
        return None
    # The common directory, not the worktree's own: a linked worktree's holds
    # no objects.
    store = _git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    try:
        extracted = fetch.extract_git(Path(store), commit, SOURCE)
    except (IndexError, ValueError, fetch.FetchError) as exc:
        # `git cat-file --batch` answered "missing": a partial clone.
        raise fetch.AddonRefused(
            f"this checkout lacks the files of {commit[:12]}; a repo library cannot be "
            "read from a partial clone"
        ) from exc
    files = {rel: entry for rel, entry in extracted.files.items() if rel != manifest.ADDON_JSON}
    if not files:
        return None
    derived = json.dumps(derive_manifest(files), indent=2, sort_keys=True) + "\n"
    files[manifest.ADDON_JSON] = ("100644", derived.encode())
    return replace(extracted, files=files)
