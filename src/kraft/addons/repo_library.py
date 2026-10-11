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
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from kraft.addons import fetch, load, manifest, update
from kraft.addons.config import RESERVED_NAMESPACE, RepoLibrariesLock, RepoLockEntry
from kraft.config import GIT_READ_ENV, ConfigError, bounded_yaml, load_repos, write_text
from kraft.worker.sandbox import harden_host_git_env

if TYPE_CHECKING:
    from kraft.addons.review import Review

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


class NoOrigin(fetch.AddonRefused):
    """The checkout has no `origin`, so it has no repo library."""


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
        raise NoOrigin(
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


_LOCK_HEADER = "# Written by `kraft repo library`. Do not edit.\n"


@dataclass(frozen=True)
class Check:
    """What one repository's repo library is, against what was accepted."""

    repo_id: str
    #: `none` (the remote has no repo library), `current`, `waiting` (a first
    #: load or a change: `review` is set), `refused` or `failed`.
    outcome: str
    review: Review | None = None
    problems: tuple[str, ...] = ()
    #: For `failed`: `network` or `auth`.
    kind: str | None = None
    remote: Remote | None = None
    #: The candidate's digest, and the lock's when this was checked.
    digest: str | None = None
    locked: str | None = None
    extracted: fetch.Extracted | None = None


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def _id_problem(repo_id: str, addons: list[load.InstalledAddon]) -> str | None:
    """Why `repo_id` cannot be a repo library's namespace, with the fix."""
    fix = "set another `id` on the repository's entry in repos.yaml"
    if repo_id == RESERVED_NAMESPACE:
        return f"repository id {repo_id!r} is Kraft's own namespace; {fix}"
    if len(repo_id) > 64:
        return f"repository id {repo_id[:20]!r}... is longer than 64 characters; {fix}"
    for addon in addons:
        if addon.repo_id is None and addon.namespace == repo_id:
            return f"repository id {repo_id!r} is the namespace of add-on {addon.id}; {fix}"
    return None


def check(
    checkout: Path,
    repo_id: str,
    config_dir: Path,
    addons_root: Path,
    *,
    repos: dict | None = None,
    default_chain: str | None = None,
) -> Check:
    """Ask the remote, and judge what it has as an add-on update is judged.
    Writes nothing. `repos` is a `repos.yaml` to judge in place of the file:
    the entry's id may be minted and not saved yet. `default_chain` is the
    entry's own, which decides whether a `default` chain here becomes the
    repo's."""
    from kraft.addons import review as review_mod

    addon_id = load.repo_library_id(repo_id)
    locked = RepoLibrariesLock.load(config_dir / RepoLibrariesLock.FILE).repos.get(repo_id)
    was = locked.digest if locked else None
    try:
        found = remote(checkout)
        extracted = extract(checkout, found.commit)
    except fetch.FetchError as exc:
        outcome = "refused" if exc.kind == "refused" else "failed"
        return Check(repo_id, outcome, problems=(str(exc),), kind=exc.kind, locked=was)
    except NoOrigin as exc:
        return Check(repo_id, "none", problems=(str(exc),), locked=was)
    except fetch.AddonRefused as exc:
        return Check(repo_id, "refused", problems=(str(exc),), locked=was)
    if extracted is None:
        return Check(repo_id, "none", remote=found, locked=was)
    digest = fetch.digest(extracted.files)
    base = Check(repo_id, "current", remote=found, digest=digest, locked=was, extracted=extracted)
    if digest == was:
        return base

    def refused(*problems: str) -> Check:
        return replace(base, outcome="refused", problems=problems)

    loaded = list(load.installed(config_dir, addons_root))
    if why := _id_problem(repo_id, loaded):
        return refused(why)
    try:
        declared = manifest.addon(
            json.loads(extracted.files[manifest.ADDON_JSON][1]), manifest.ADDON_JSON
        )
        manifest.check_addon(declared, None, extracted.files)
    except manifest.ManifestError as exc:
        return refused(str(exc))
    others = {p.namespace for p in loaded if p.id != addon_id} | {repo_id}
    problems = update.problems(declared, extracted.files, other_namespaces=others)
    if why := load.instance_problem(
        repo_id, declared.requires, config_dir, repos=repos, repo_library=True
    ):
        problems.append(why)
    if problems:
        return refused(*problems)

    old = None
    store = addons_root / "store" / was.removeprefix("sha256:") if was else None
    if store is not None and store.is_dir():
        try:
            old = fetch.extract_dir(store)
        except Exception:  # noqa: BLE001 -- an edited store can break in any way
            old = None
    reviewed = review_mod.review(addon_id, old, extracted)
    reach = list(reviewed.reach)
    if was and old is None:
        reach.insert(0, "the accepted version's files could not be read to compare with")
    content = [
        f"read from {found.url} at {found.commit[:12]} ({found.branch})",
        *reviewed.content,
    ]
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / digest.removeprefix("sha256:")
        for rel, (_mode, data) in extracted.files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        candidate = load.InstalledAddon(addon_id, NAME, repo_id, _short(digest), root)
        state = update.state_of(config_dir, loaded)
        after = update.state_of(config_dir, [*(p for p in loaded if p.id != addon_id), candidate])
        if broken := update.breaks(state, after, config_dir, repo_id):
            return replace(
                refused(*(why.replace(str(root), addon_id) for why in broken)),
                review=reviewed,
            )
        if was is None:
            reach += [
                f"{where}: {key} {value!r} is now read from this repo library"
                for where, key, _kind, value in update.references(config_dir)
                if update._qualifier(value) == repo_id
            ]
            own = f"{repo_id}:default"
            if (
                default_chain in (None, "default")
                and after.library is not None
                and own in after.library.chain_ids
            ):
                reach.append(f"becomes the chain new items in this repo run: {own}")
        if after.library is not None:
            more_reach, more_content = review_mod.local_changes(
                state.library, after.library, repo_id
            )
            reach += more_reach
            content += more_content
    return replace(
        base,
        outcome="waiting",
        review=replace(
            reviewed,
            old_version=_short(was) if was else None,
            new_version=_short(digest),
            reach=tuple(reach),
            content=tuple(content),
        ),
    )


def _write(config_dir: Path, lock: RepoLibrariesLock) -> None:
    write_text(
        config_dir / RepoLibrariesLock.FILE,
        _LOCK_HEADER + yaml.safe_dump(lock.model_dump(mode="json"), sort_keys=False),
    )


def accept(found: Check, config_dir: Path, addons_root: Path) -> None:
    """Take a `waiting` check: write its store and its lock entry. Raises
    `update.Busy` when the lock is not the one the check read, or another
    add-on change is running."""
    assert found.outcome == "waiting" and found.extracted and found.remote and found.digest
    with update.write_lock(addons_root):
        lock = RepoLibrariesLock.load(config_dir / RepoLibrariesLock.FILE)
        now = lock.repos.get(found.repo_id)
        if (now.digest if now else None) != found.locked:
            raise update.Busy("the repo library changed while you reviewed; check again")
        fetch.write_store(addons_root, found.extracted)
        entry = RepoLockEntry(
            git=found.remote.url,
            branch=found.remote.branch,
            commit=found.remote.commit,
            tree=found.extracted.tree,
            digest=found.digest,
            updated_at=datetime.now(UTC),
        )
        _write(config_dir, RepoLibrariesLock(repos={**lock.repos, found.repo_id: entry}))
    update.clear_status(addons_root, {load.repo_library_id(found.repo_id)})


def drop(repo_id: str, config_dir: Path, addons_root: Path) -> bool:
    """Forget `repo_id`'s accepted repo library; whether there was one. The
    store is left to the garbage collector."""
    with update.write_lock(addons_root):
        lock = RepoLibrariesLock.load(config_dir / RepoLibrariesLock.FILE)
        if repo_id not in lock.repos:
            return False
        remaining = {k: v for k, v in lock.repos.items() if k != repo_id}
        _write(config_dir, RepoLibrariesLock(repos=remaining))
    update.clear_status(addons_root, {load.repo_library_id(repo_id)})
    return True


def check_all(
    config_dir: Path, addons_root: Path, cancel: threading.Event | None = None
) -> dict[str, dict]:
    """Ask the remote about every accepted repo library whose repository is
    still connected, and record what it said in `status.json`: `up to date`,
    `waiting` or `failed`. Applies nothing: a person takes a waiting change
    with `kraft repo library`. A repository nobody accepted a library for is
    not asked about. `cancel` (set at shutdown) stops between repositories."""
    try:
        lock = RepoLibrariesLock.load(config_dir / RepoLibrariesLock.FILE)
        paths = {r.id: Path(r.path) for r in load_repos(config_dir / "repos.yaml") if r.id}
    except ConfigError:
        return {}  # the load says why
    outcomes: dict[str, dict] = {}
    for repo_id in lock.repos:
        if repo_id not in paths or (cancel is not None and cancel.is_set()):
            continue
        found = check(paths[repo_id], repo_id, config_dir, addons_root)
        if found.outcome == "current":
            outcome: dict = {"outcome": "up to date"}
        elif found.outcome == "waiting":
            # `content[0]` is "read from <url> at <commit>": not what changed.
            lines = [*found.review.reach, *found.review.content[1:]][:3]
            said = "; ".join(line.splitlines()[0] for line in lines if line)
            outcome = {"outcome": "waiting", "message": said or "its files changed"}
        elif found.outcome == "none":
            outcome = {"outcome": "waiting", "message": "origin no longer has a repo library"}
        else:
            outcome = {
                "outcome": "failed",
                "kind": found.kind or "refused",
                "message": "; ".join(found.problems),
            }
        outcomes[load.repo_library_id(repo_id)] = outcome
    if outcomes:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        status = update.read_status(addons_root) | {
            key: {"at": now, "kind": None, "message": None, **outcome}
            for key, outcome in outcomes.items()
        }
        addons_root.mkdir(parents=True, exist_ok=True)
        write_text(addons_root / update.STATUS_FILE, json.dumps(status, indent=2) + "\n")
    return outcomes
