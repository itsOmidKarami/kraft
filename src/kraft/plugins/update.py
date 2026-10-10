"""Installing and updating plugins: judging a candidate before anything is
written, and writing only what someone accepted.

`update` runs fetch, check, lint, review, accept and commit. Anything short of
an accepted review leaves `plugins.yaml`, the lock and the store as they were.

`check` is what a plugin's own files may not say, whatever instance takes it:
a plugin never runs a command, never grants a permission, runs only on the
harnesses it lists, and reaches into no other plugin. The rules read the
files as the author wrote them. A plugin is self-contained, so everything one
of its chains can inherit is in these same files: checking each file checks
every chain after `extends:` expansion, and the components no chain uses yet.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from kraft.config import (
    ConfigError,
    bounded_yaml,
    first_error,
    read_yaml,
    write_text,
    write_yaml,
)
from kraft.plugins import fetch, load, manifest
from kraft.plugins.config import (
    CollectionConfig,
    LockEntry,
    PluginEntry,
    PluginsConfig,
    PluginsLock,
)
from kraft.plugins.manifest import PluginManifest

if TYPE_CHECKING:
    from kraft.plugins.review import Review
    from kraft.templates.library import TemplateLibrary

#: How long a second writer waits for the update lock before it fails.
LOCK_WAIT_S = 5
_LOCK_HEADER = "# Written by `kraft admin plugin`. Do not edit.\n"

#: The `policy:` keys a plugin may set: how long, how much, how many times.
#: An allowlist, so a key a later Kraft adds is refused until it is listed.
LIMITS = frozenset(
    {
        "time_cap_minutes",
        "total_time_cap_minutes",
        "timeout_minutes",
        "max_attempts",
        "budget_usd",
        "token_budget",
        "deny_tools",
    }
)
#: Kraft's own qualifier: `kraft:spec` is a method Kraft ships.
_KRAFT = "kraft"
_SECTIONS = ("steering", "tasks", "steps", "nodes")


class Refused(Exception):
    """A plugin Kraft will not take. `problems` is every reason, each naming
    its file and key."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _mappings(data: object, at: str) -> Iterator[tuple[str, Mapping]]:
    """Every mapping under `data`, with the key path it sits at."""
    if isinstance(data, Mapping):
        yield at, data
        for key, value in data.items():
            yield from _mappings(value, f"{at}.{key}" if at else str(key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            label = value.get("id", index) if isinstance(value, Mapping) else index
            yield from _mappings(value, f"{at}[{label}]")


def _components(rel: str, data: object) -> Iterator[tuple[str, Mapping]]:
    """The authored components of one file. A `library.yaml` section maps
    names to definitions, so its keys are names, never fields: a task named
    `policy` is not a policy block."""
    if not isinstance(data, Mapping):
        return
    if rel == "library.yaml":
        for section in _SECTIONS:
            entries = data.get(section)
            for name, body in entries.items() if isinstance(entries, Mapping) else ():
                yield from _mappings(body, f"{section}.{name}")
    elif rel == "profiles.yaml":
        entries = data.get("profiles")
        for name, body in entries.items() if isinstance(entries, Mapping) else ():
            yield from _mappings(body, f"profiles.{name}")
    else:
        yield from _mappings(data, "")


def _moved(collection: CollectionConfig, locked: LockEntry) -> bool:
    """Whether the collection's URL is another than the one locked. A lock
    that recorded none says nothing: the next update records it."""
    return locked.git is not None and collection.git != locked.git


def _qualifier(value: object) -> str | None:
    if isinstance(value, str) and ":" in value:
        return value.split(":", 1)[0]
    return None


def problems(
    found: PluginManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
) -> list[str]:
    """Every reason this plugin's content is refused; empty when it is taken.
    `other_namespaces` are the installed plugins' namespaces, this one's own
    left out: a skill reference into one of them is a plugin dependency."""
    out: list[str] = []
    allowed_harnesses = set(found.requires.harnesses)
    for rel in sorted(files):
        if not (rel in ("library.yaml", "profiles.yaml") or rel.startswith("chains/")):
            continue
        try:
            data = bounded_yaml(files[rel][1].decode(), [20_000])
        except (ValueError, yaml.YAMLError) as exc:
            out.append(f"{rel}: cannot parse: {exc}")
            continue
        for at, component in _components(rel, data):
            where = f"{rel}: {at}" if at else rel
            if component.get("kind") == "subprocess":
                out.append(
                    f"{where}: a plugin may not carry a subprocess task; a shell command runs "
                    "outside the permission gate an agent works under"
                )
            policy = component.get("policy")
            for key in policy if isinstance(policy, Mapping) else ():
                if key not in LIMITS:
                    out.append(
                        f"{where}: policy.{key} is not a limit; a plugin may set only "
                        f"{', '.join(sorted(LIMITS))}. Permissions stay in the instance's "
                        "policy.yaml"
                    )
            harness = component.get("harness")
            if isinstance(harness, str) and harness not in allowed_harnesses:
                out.append(
                    f"{where}: harness {harness!r} is not listed in requires.harnesses "
                    f"({sorted(allowed_harnesses)})"
                )
            steering = component.get("steering")
            references = [
                ("extends", component.get("extends")),
                ("profile", component.get("profile")),
                *(("steering", name) for name in (steering if isinstance(steering, list) else ())),
            ]
            for key, value in references:
                qualifier = _qualifier(value)
                if qualifier is not None and qualifier != found.name:
                    out.append(
                        f"{where}: {key} {value!r} reaches outside the plugin; a plugin is "
                        "self-contained"
                    )
            skill = component.get("skill")
            if _qualifier(skill) in set(other_namespaces) - {found.name, _KRAFT}:
                out.append(
                    f"{where}: skill {skill!r} names another installed Kraft plugin; there are "
                    "no plugin dependencies"
                )
    return out


def check(
    found: PluginManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
) -> None:
    """Raise `Refused` when this plugin's content may not be installed."""
    if found_problems := problems(found, files, other_namespaces=other_namespaces):
        raise Refused(found_problems)


class Busy(Exception):
    """Plugin state could not be written: another writer holds the update
    lock, or the plugin files changed while a review was open."""


@dataclass(frozen=True)
class Result:
    """What `update` did with one plugin."""

    plugin_id: str
    #: `applied`; `current` (nothing to take); `declined` (reviewed, not
    #: accepted: an update is waiting); `refused` (a check failed);
    #: `unpublished` (gone from its collection, kept on its lock); `failed`
    #: (could not be checked or written).
    outcome: str
    review: Review | None = None
    #: Why, for `refused`, `unpublished` and `failed`.
    problems: tuple[str, ...] = ()
    #: For `failed`: `network`, `auth`, `refused` or `busy`.
    kind: str | None = None
    #: For `current`: its files changed at an unchanged version.
    note: str | None = None


@contextmanager
def write_lock(plugins_dir: Path, wait: float | None = None) -> Iterator[None]:
    """The update lock: held by every write to `plugins.yaml`, the lock file
    or the store. A second writer waits `wait` seconds (`LOCK_WAIT_S` when
    None), then raises `Busy`."""
    plugins_dir.mkdir(parents=True, exist_ok=True)
    with open(plugins_dir / ".lock", "w") as fh:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("another plugin change is running; try again") from None
                time.sleep(0.05)
        yield


@dataclass(frozen=True)
class State:
    """What resolves in the instance with one set of plugins loaded."""

    library: TemplateLibrary | None
    chains: frozenset[str]
    #: `(chain or None, message)` of every lint issue.
    issues: frozenset[tuple[str | None, str]]
    steering: frozenset[str]
    profiles: frozenset[str]
    table_error: str | None

    def resolves(self, kind: str, value: str) -> bool:
        return (
            value
            in {"chain": self.chains, "steering": self.steering, "profile": self.profiles}[kind]
        )


def state_of(config_dir: Path, plugins: Sequence[load.InstalledPlugin]) -> State:
    from kraft import harness
    from kraft.api import config_check
    from kraft.paths import default_skills_dir
    from kraft.policy import PolicyInput
    from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    skills = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    # A policy.yaml that does not load is not caught: a candidate is never
    # judged without the maxima.
    policy_file = config_dir / "policy.yaml"
    policy = PolicyInput.from_yaml(policy_file).instance_policy() if policy_file.exists() else None
    report = config_check.lint_report(
        config_dir, skills_dir=skills, instance_policy=policy, plugins=plugins
    )
    try:
        library = TemplateLibrary.from_yaml_dir(config_dir, skills_dir=skills, plugins=plugins)
    except TemplateLibraryError:
        library = None
    profiles: frozenset[str] = frozenset()
    table_error = None
    try:
        table = HarnessProfileTable.from_yaml(
            config_dir / "harnesses.yaml", harnesses=harness.load(None).valid, plugins=plugins
        )
        profiles = frozenset(table.agent_profiles)
    except TemplateEnvironmentError as exc:
        table_error = str(exc)
    return State(
        library=library,
        chains=frozenset(report["chains"]),
        issues=frozenset((i["chain"], i["message"]) for i in report["issues"]),
        steering=frozenset(library.steering) if library else frozenset(),
        profiles=profiles,
        table_error=table_error,
    )


def references(config_dir: Path) -> Iterator[tuple[str, str, str, str]]:
    """`(where, key, kind, value)` for every qualified reference the instance's
    own files make: its library and chains, and the chain, steering and
    fallback references of `repos.yaml`, `intake.yaml`, `policy.yaml` and
    `harnesses.yaml`. `kind` is what the others must resolve as; None for a
    library reference, which lint judges."""
    local = ["library.yaml", *sorted(f"chains/{p.name}" for p in config_dir.glob("chains/*.yaml"))]
    for rel in (*local, "repos.yaml", "intake.yaml", "policy.yaml", "harnesses.yaml"):
        try:
            data = read_yaml(config_dir / rel)
        except ConfigError:
            continue  # that file's own check says why
        keys = (
            {"extends": None, "skill": None, "profile": None}
            if rel in local
            else {"default_chain": "chain", "chain": "chain", "profile": "profile"}
        )
        for at, mapping in _mappings(data, ""):
            found = [(key, kind, mapping.get(key)) for key, kind in keys.items()]
            steering = mapping.get("steering")
            found += [
                ("steering", None if rel in local else "steering", name)
                for name in (steering if isinstance(steering, list) else ())
            ]
            for key, kind, value in found:
                if _qualifier(value) is not None:
                    yield f"{rel}: {at}" if at else rel, key, kind, value


def breaks(
    before: State, after: State, config_dir: Path, namespace: str | None = None
) -> list[str]:
    """Why what resolves in `before` would not in `after`. `namespace` is the
    candidate plugin's, whose own chains must resolve too; None when no
    plugin's content changes (a `plugins.yaml` edit)."""
    out = (
        [f"harnesses.yaml: {after.table_error}"]
        if after.table_error and not before.table_error
        else []
    )
    for chain, message in sorted(after.issues - before.issues, key=str):
        if chain is None:
            out.append(message)
        elif namespace is not None and chain.startswith(f"{namespace}:"):
            out.append(f"its chain {chain} does not resolve: {message}")
        elif chain in before.chains:
            out.append(f"chain {chain} would stop resolving: {message}")
    for where, key, kind, value in references(config_dir):
        if kind and before.resolves(kind, value) and not after.resolves(kind, value):
            out.append(f"{where}: {key} {value!r} would stop resolving")
    return out


def _catalogue(
    name: str, collection: CollectionConfig, plugins_dir: Path
) -> tuple[Path | None, str | None, manifest.CollectionManifest]:
    """The collection fetched at its ref: its mirror and commit (None for a
    directory) and its `collection.json`."""
    where = manifest.COLLECTION_JSON
    if collection.git is not None:
        mirror, commit = fetch.fetch(plugins_dir, name, collection.git, collection.ref)
        data = fetch.read_file(mirror, commit, where, fetch.MAX_JSON)
        source = fetch.redact(collection.git)
    else:
        mirror, commit, source = None, None, str(collection.path)
        try:
            data = (Path(source) / where).read_bytes()
        except OSError as exc:
            raise fetch.FetchError("refused", f"{source}: {exc}") from exc
    try:
        text = data.decode()
    except UnicodeDecodeError as exc:
        raise fetch.PluginRefused(f"{source}: {where} is not valid UTF-8") from exc
    listed = manifest.collection(manifest.parse(text, where), where)
    if listed.name != name:
        raise fetch.PluginRefused(
            f"collection at {source} now calls itself {listed.name}; re-add it under that name"
        )
    return mirror, commit, listed


@dataclass(frozen=True)
class _Candidate:
    plugin_id: str
    extracted: fetch.Extracted
    mirror: Path | None
    locks_as: LockEntry


def update(
    ids: Sequence[str] | None = None,
    *,
    accept: Callable[[Review], bool],
    re_install: bool = False,
    entries: Mapping[str, PluginEntry | bool] | None = None,
    config_dir: Path | None = None,
    plugins_root: Path | None = None,
) -> list[Result]:
    """Install or update `ids` (every plugin in `plugins.yaml` when None), one
    `Result` each. `accept` is asked once per plugin that has something to
    take, with its review. `entries` are new or changed `plugins.yaml` entries
    (an install, an alias), held in memory and written only for a plugin that
    was accepted. `re_install` takes the commit the ref resolves to now even
    at an unchanged version. Raises `Refused` when `entries` do not fit
    `plugins.yaml`; `ConfigError` or `PolicyError` when `plugins.yaml`, the lock
    or `policy.yaml` cannot be read."""
    from kraft import paths
    from kraft import update as kraft_update
    from kraft.plugins import review as review_mod

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    plugins_root = Path(plugins_root) if plugins_root is not None else load.plugins_dir()
    files = (config_dir / PluginsConfig.FILE, config_dir / PluginsLock.FILE)
    seen = [f.read_bytes() if f.exists() else None for f in files]
    written = read_yaml(files[0], {})
    lock = PluginsLock.load(files[1])
    new_entries = {
        plugin_id: e
        if isinstance(e, bool)
        else e.model_dump(by_alias=True, exclude_defaults=True) or True
        for plugin_id, e in (entries or {}).items()
    }
    try:
        config = PluginsConfig.model_validate(
            {**written, "plugins": {**(written.get("plugins") or {}), **new_entries}}
        )
    except ValidationError as exc:
        raise Refused([first_error(exc, PluginsConfig.FILE)]) from exc

    running = kraft_update.installed()
    catalogues: dict[str, tuple | Exception] = {}
    results: dict[str, Result] = {}
    accepted: list[_Candidate] = []
    with tempfile.TemporaryDirectory() as scratch:
        loaded = list(load.installed(config_dir, plugins_root))
        state = state_of(config_dir, loaded)
        for plugin_id in ids if ids is not None else list(config.plugins):
            if plugin_id not in config.plugins:
                results[plugin_id] = Result(
                    plugin_id, "refused", problems=("is not in plugins.yaml; install it first",)
                )
                continue
            name, collection_name = plugin_id.split("@", 1)
            collection = config.collections[collection_name]
            locked = lock.plugins.get(plugin_id)
            namespace = config.namespace(plugin_id)
            try:
                if collection_name not in catalogues:
                    try:
                        catalogues[collection_name] = _catalogue(
                            collection_name, collection, plugins_root
                        )
                    except (fetch.FetchError, fetch.PluginRefused, manifest.ManifestError) as exc:
                        catalogues[collection_name] = exc
                if isinstance(catalogues[collection_name], Exception):
                    raise catalogues[collection_name]
                mirror, commit, listed = catalogues[collection_name]
                entry = next((e for e in listed.plugins if e.name == name), None)
                if entry is None and locked is not None:
                    results[plugin_id] = Result(
                        plugin_id,
                        "unpublished",
                        problems=(
                            f"is no longer published by {collection_name}; "
                            f"it stays on {locked.version}",
                        ),
                    )
                    continue
                if entry is None:
                    raise fetch.PluginRefused(f"collection {collection_name} has no plugin {name}")
                if mirror is not None:
                    extracted = fetch.extract_git(mirror, commit, entry.source)
                else:
                    extracted = fetch.extract_dir(
                        Path(collection.path) / entry.source.removeprefix("./")
                    )
                if manifest.PLUGIN_JSON not in extracted.files:
                    raise manifest.ManifestError(f"no {manifest.PLUGIN_JSON}")
                found = manifest.plugin(
                    manifest.parse(
                        extracted.files[manifest.PLUGIN_JSON][1].decode(), manifest.PLUGIN_JSON
                    ),
                    manifest.PLUGIN_JSON,
                )
                manifest.check_plugin(found, name, extracted.files)
                if not manifest.is_source_build(running):
                    if why := manifest.kraft_compatible(found.requires.kraft, running):
                        raise Refused([f"{found.version} {why}; this is Kraft {running}"])
                holders = {
                    held.namespace: other
                    for other, held in lock.plugins.items()
                    if other != plugin_id
                }
                if namespace in holders:
                    raise Refused(
                        [
                            f"namespace {namespace!r} is still held by {holders[namespace]}; "
                            "update that plugin first"
                        ]
                    )
                if why := load.instance_problem(namespace, found.requires, config_dir):
                    raise Refused([why])
                others = set(holders) | {
                    config.namespace(o) for o in config.plugins if o != plugin_id
                }
                check(found, extracted.files, other_namespaces=others)
            except fetch.FetchError as exc:
                results[plugin_id] = Result(
                    plugin_id, "failed", problems=(str(exc),), kind=exc.kind
                )
                continue
            except Refused as exc:
                results[plugin_id] = Result(plugin_id, "refused", problems=tuple(exc.problems))
                continue
            except (fetch.PluginRefused, manifest.ManifestError, OSError) as exc:
                results[plugin_id] = Result(plugin_id, "refused", problems=(str(exc),))
                continue

            digest = fetch.digest(extracted.files)
            pending = (
                locked is None
                or re_install
                or found.version != locked.version
                or collection.ref != locked.ref
                or _moved(collection, locked)
                or namespace != locked.namespace
                or (mirror is None and digest != locked.digest)
            )
            if not pending:
                note = None
                if extracted.tree != locked.tree:
                    note = (
                        f"version {locked.version} unchanged, but its files changed at "
                        f"{commit[:12]}; --re-install to take it"
                    )
                results[plugin_id] = Result(plugin_id, "current", note=note)
                continue

            store = (
                plugins_root / "store" / locked.digest.removeprefix("sha256:") if locked else None
            )
            try:
                old = fetch.extract_dir(store) if store is not None and store.is_dir() else None
                reviewed = review_mod.review(plugin_id, old, extracted)
            except (fetch.PluginRefused, manifest.ManifestError, OSError, KeyError, ValueError):
                # An edited or damaged store: everything in the candidate is shown
                # as new, and the review says the comparison is missing.
                reviewed = review_mod.review(plugin_id, None, extracted)
                if locked is not None:
                    unread = f"the installed {locked.version} could not be read to compare with"
                    reviewed = replace(reviewed, reach=(unread, *reviewed.reach))
            if locked is not None and _moved(collection, locked):
                moved = f"collection URL changed: {fetch.redact(locked.git or '')} -> " + (
                    fetch.redact(collection.git or "")
                )
                reviewed = replace(reviewed, reach=(moved, *reviewed.reach))

            root = Path(scratch) / digest.removeprefix("sha256:")
            for rel, (_mode, data) in extracted.files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            candidate = load.InstalledPlugin(plugin_id, name, namespace, found.version, root)
            would_load = [p for p in loaded if p.id != plugin_id] + [candidate]
            after = state_of(config_dir, would_load)
            if broken := breaks(state, after, config_dir, namespace):
                results[plugin_id] = Result(
                    plugin_id,
                    "refused",
                    review=reviewed,
                    problems=tuple(why.replace(str(root), plugin_id) for why in broken),
                )
                continue
            captured = (
                [
                    f"{where}: {key} {value!r} is now read from this plugin"
                    for where, key, _kind, value in references(config_dir)
                    if _qualifier(value) == namespace
                ]
                if locked is None or locked.namespace != namespace
                else []
            )
            reach, content = (
                review_mod.local_changes(state.library, after.library, namespace)
                if after.library is not None
                else ([], [])
            )
            reviewed = replace(
                reviewed,
                reach=(*reviewed.reach, *captured, *reach),
                content=(*reviewed.content, *content),
            )
            if not accept(reviewed):
                results[plugin_id] = Result(plugin_id, "declined", review=reviewed)
                continue
            loaded, state = would_load, after
            results[plugin_id] = Result(plugin_id, "applied", review=reviewed)
            accepted.append(
                _Candidate(
                    plugin_id,
                    extracted,
                    mirror,
                    LockEntry(
                        namespace=namespace,
                        ref=collection.ref,
                        git=collection.git,
                        commit=commit,
                        tree=extracted.tree,
                        source=entry.source,
                        digest=digest,
                        version=found.version,
                        updated_at=datetime.now(UTC),
                    ),
                )
            )

    if accepted:
        try:
            with write_lock(plugins_root):
                if [f.read_bytes() if f.exists() else None for f in files] != seen:
                    raise Busy("plugins changed while you reviewed; run update again")
                for done in accepted:
                    fetch.write_store(plugins_root, done.extracted)
                    if done.mirror is not None:
                        fetch.pin(done.mirror, done.locks_as.commit)
                took = {
                    d.plugin_id: new_entries[d.plugin_id]
                    for d in accepted
                    if d.plugin_id in new_entries
                }
                if took:
                    # plugins.yaml first: a crash before the lock leaves a
                    # known "listed, not installed" entry.
                    write_yaml(
                        files[0], {**written, "plugins": {**(written.get("plugins") or {}), **took}}
                    )
                # A lock entry `plugins.yaml` no longer lists is dropped here.
                kept = {i: e for i, e in lock.plugins.items() if i in config.plugins}
                locks = PluginsLock(plugins=kept | {d.plugin_id: d.locks_as for d in accepted})
                write_text(
                    files[1],
                    _LOCK_HEADER + yaml.safe_dump(locks.model_dump(mode="json"), sort_keys=False),
                )
        except Busy as exc:
            for done in accepted:
                results[done.plugin_id] = replace(
                    results[done.plugin_id], outcome="failed", problems=(str(exc),), kind="busy"
                )
        else:
            # `auto_update` records its own outcome after this.
            clear_status(plugins_root, {done.plugin_id for done in accepted})
    return list(results.values())


#: Where each plugin's last auto-update is recorded, under `run/plugins/`:
#: `/health` and doctor both read it, so it survives a restart and doctor sees
#: it with the server down.
STATUS_FILE = "status.json"


def may_apply_unattended(review: Review) -> str | None:
    """Why an auto-update holds this review for a person; None when it may
    apply it on its own. Any reach call-out holds it, and a downgrade and a
    move onto a pre-release are reach call-outs themselves (`review.review`).
    `update` only asks about a version that differs, so what is left is a
    newer release that widens nothing."""
    if review.old_version is None:
        return "it is not installed"
    if review.reach:
        lines = [entry.splitlines()[0] for entry in review.reach]
        more = f"; and {len(lines) - 3} more" if len(lines) > 3 else ""
        return "; ".join(lines[:3]) + more
    return None


def clear_status(plugins_root: Path, plugin_ids: Collection[str]) -> None:
    """Forget the last auto-update outcome of `plugin_ids`: a person has just
    acted on them (applied the update, uninstalled the plugin), so "held" or
    "failed" no longer says anything true."""
    import json

    status = read_status(plugins_root)
    left = {plugin_id: entry for plugin_id, entry in status.items() if plugin_id not in plugin_ids}
    if left != status:
        write_text(Path(plugins_root) / STATUS_FILE, json.dumps(left, indent=2) + "\n")


def read_status(plugins_root: Path) -> dict[str, dict]:
    """Plugin id to its last auto-update `{at, outcome, kind, message}`; empty
    when none has run or the file does not read."""
    import json

    try:
        data = json.loads((Path(plugins_root) / STATUS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def auto_update(
    config_dir: Path | None = None, plugins_root: Path | None = None
) -> dict[str, dict]:
    """Update every locked plugin whose auto-update is on (its collection's, or
    its own), applying only what `may_apply_unattended` allows, and record
    each one's outcome in `status.json`: `applied`, `up to date`, `held` or
    `failed` (with `kind`: `network`, `auth`, `refused` or `busy`). Never
    installs, never re-installs, and never applies a ref or alias changed by
    hand: those wait for a person's `update`. A collection none of whose
    plugins auto-update is not fetched."""
    import json

    from kraft import paths

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    plugins_root = Path(plugins_root) if plugins_root is not None else load.plugins_dir()
    try:
        config = PluginsConfig.load(config_dir / PluginsConfig.FILE)
        lock = PluginsLock.load(config_dir / PluginsLock.FILE)
    except ConfigError:
        return {}  # the load says why
    outcomes: dict[str, dict] = {}
    ids = []
    for plugin_id, locked in lock.plugins.items():
        if plugin_id not in config.plugins:
            continue
        collection = config.collections[plugin_id.split("@", 1)[1]]
        if not (collection.auto_update or config.entry(plugin_id).auto_update):
            continue
        pending = [
            what
            for what, changed in (
                ("ref", collection.ref != locked.ref),
                ("collection URL", _moved(collection, locked)),
                ("alias", config.namespace(plugin_id) != locked.namespace),
            )
            if changed
        ]
        if pending:
            outcomes[plugin_id] = {
                "outcome": "held",
                "message": f"{pending[0]} change pending; run kraft admin plugin update",
            }
        else:
            ids.append(plugin_id)
    held: dict[str, str] = {}

    def accept(review: Review) -> bool:
        why = may_apply_unattended(review)
        if why is not None:
            held[review.plugin_id] = why
        return why is None

    results = (
        update(ids, accept=accept, config_dir=config_dir, plugins_root=plugins_root) if ids else []
    )
    for result in results:
        said = "; ".join(result.problems)
        outcomes[result.plugin_id] = {
            "applied": {"outcome": "applied"},
            "current": {"outcome": "up to date"},
            "unpublished": {"outcome": "up to date", "message": said},
            "declined": {
                "outcome": "held",
                "message": f"{held.get(result.plugin_id)}; run kraft admin plugin update",
            },
            "refused": {"outcome": "failed", "kind": "refused", "message": said},
            "failed": {"outcome": "failed", "kind": result.kind, "message": said},
        }[result.outcome]
    if outcomes:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        status = read_status(plugins_root) | {
            plugin_id: {"at": now, "kind": None, "message": None, **outcome}
            for plugin_id, outcome in outcomes.items()
        }
        plugins_root.mkdir(parents=True, exist_ok=True)
        write_text(plugins_root / STATUS_FILE, json.dumps(status, indent=2) + "\n")
    return outcomes
