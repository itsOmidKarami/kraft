"""Installing and updating add-ons: judging a candidate before anything is
written, and writing only what someone accepted.

`update` runs fetch, check, lint, review, accept and commit. Anything short of
an accepted review leaves `addons.yaml`, the lock and the store as they were.

`check` is what an add-on's own files may not say, whatever instance takes it:
an add-on never runs a command, never grants a permission, runs only on the
harnesses it lists, and reaches into no add-on but its parent. The rules read
the files as the author wrote them. What a root add-on's chains can inherit is
in these same files, and what a child's inherit is in its ancestors', each
checked when it was installed: checking each file checks every chain after
`extends:` expansion, and the components no chain uses yet. A child is judged
with its ancestors in place (`problems(ancestors=...)`, `_ancestors`).
"""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from kraft.addons import fetch, inherit, load, manifest
from kraft.addons.config import (
    ADDON_ID_PATTERN,
    AddonEntry,
    AddonsConfig,
    AddonsLock,
    CollectionConfig,
    LockEntry,
)
from kraft.addons.manifest import AddonManifest
from kraft.config import (
    ConfigError,
    bounded_yaml,
    connected_entry,
    first_error,
    read_yaml,
    write_text,
    write_yaml,
)

if TYPE_CHECKING:
    from kraft.addons.review import Review
    from kraft.templates.library import TemplateLibrary

#: How long a second writer waits for the update lock before it fails.
LOCK_WAIT_S = 5
_LOCK_HEADER = "# Written by `kraft admin addon`. Do not edit.\n"

#: The `policy:` keys an add-on may set: how long, how much, how many times.
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
    """An add-on Kraft will not take. `problems` is every reason, each naming
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
    """Whether the collection is read from another place than the one locked.
    A git lock that recorded no URL says nothing: the next update records it.
    A directory's lock has no commit, so a URL now is another source."""
    if locked.commit is None:
        return collection.git is not None
    return locked.git is not None and collection.git != locked.git


def _qualifier(value: object) -> str | None:
    if isinstance(value, str) and ":" in value:
        return value.split(":", 1)[0]
    return None


#: `AddonId`'s pattern: `<addon>@<collection>`.
_ADDON_ID = re.compile(ADDON_ID_PATTERN)


def _listed_addons(
    found: AddonManifest,
    files: Mapping[str, tuple[str, bytes]],
    repo_library: str | None,
    out: list[str],
) -> dict[str, str]:
    """A repo library's `addons:` list as add-on name to id, with every reason
    it is refused appended to `out` (`a-repo-librarys-addons-list-is-well-
    formed`). An add-on may not have the key at all (`an-addon-lists-no-addons`):
    there are no add-on dependencies."""
    entry = files.get("library.yaml")
    try:
        data = bounded_yaml(entry[1].decode(), [20_000]) if entry else None
    except (ValueError, yaml.YAMLError):
        return {}  # `problems` says the file cannot be parsed
    if not isinstance(data, Mapping) or "addons" not in data:
        return {}
    if not repo_library:
        out.append(
            "library.yaml: addons: an add-on may not list add-ons; there are no add-on dependencies"
        )
        return {}
    raw = data["addons"]
    if not isinstance(raw, list):
        out.append("library.yaml: addons: must be a list of add-on ids (<addon>@<collection>)")
        return {}
    listed: dict[str, str] = {}
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not _ADDON_ID.fullmatch(value):
            out.append(
                f"library.yaml: addons[{index}]: {value!r} is not an add-on id "
                "(<addon>@<collection>)"
            )
            continue
        name = value.split("@", 1)[0]
        if name == found.name:
            out.append(f"library.yaml: addons[{index}]: {name!r} is this library's own qualifier")
        elif name in listed:
            out.append(
                f"library.yaml: addons: lists two add-ons named {name}: {listed[name]} and {value}"
            )
        else:
            listed[name] = value
    return listed


def problems(
    found: AddonManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
    repositories: Collection[str] = (),
    repo_library: str | None = None,
    ancestors: Sequence[load.InstalledAddon] = (),
) -> list[str]:
    """Every reason this add-on's content is refused; empty when it is taken.
    `other_namespaces` are the installed add-ons' namespaces, this one's own
    left out: a skill reference into one of them is an add-on dependency.
    `repositories` are the connected repository ids: each is reserved for
    that repository's library, whether or not one is accepted.
    `repo_library`: the repository id, when the content is a repo library's,
    whose `addons:` list lets a chain's top-level `extends` name a listed
    add-on (`a-repo-library-chain-may-extend-a-listed-addon-chain`).
    A child add-on may name its parent (`requires.addons`) by the parent's
    name wherever a qualified name goes, and no other add-on
    (`an-addon-reaches-only-its-parent`). `ancestors` (parent first, root
    last) are the add-ons it builds on: given, a `remove:` name the parent's
    merged library lacks is refused, not only recorded as stale."""
    out: list[str] = []
    listed = _listed_addons(found, files, repo_library, out)
    parent_id = found.parent[0] if found.parent and not repo_library else None
    parent_name = parent_id.split("@", 1)[0] if parent_id else None
    out += _removal_problems(found, files, parent_id, ancestors)
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
                    f"{where}: an add-on may not carry a subprocess task; a shell command runs "
                    "outside the permission gate an agent works under"
                )
            policy = component.get("policy")
            for key in policy if isinstance(policy, Mapping) else ():
                if key not in LIMITS:
                    out.append(
                        f"{where}: policy.{key} is not a limit; an add-on may set only "
                        f"{', '.join(sorted(LIMITS))}. Permissions stay in the instance's "
                        "policy.yaml"
                    )
            harness = component.get("harness")
            if isinstance(harness, str) and harness not in allowed_harnesses:
                out.append(
                    f"{where}: harness {harness!r} is not listed in requires.harnesses "
                    f"({sorted(allowed_harnesses)})"
                )
            reach = f"an add-on reaches no add-on but its parent ({parent_id})"
            steering = component.get("steering")
            references = [
                ("extends", component.get("extends")),
                ("profile", component.get("profile")),
                *(("steering", name) for name in (steering if isinstance(steering, list) else ())),
            ]
            for key, value in references:
                qualifier = _qualifier(value)
                if qualifier is None or qualifier in (found.name, parent_name):
                    continue
                # `at` is empty only at a chain file's top level: a library or
                # profiles entry always sits under its section.
                top = key == "extends" and at == ""
                if top and qualifier in listed:
                    continue
                if repo_library and qualifier == repo_library:
                    # Its own id: a bare name already means its own declaration.
                    hint = (
                        f"; a bare name in a repo library is its own, so write "
                        f"{value.split(':', 1)[1]!r}"
                    )
                elif repo_library and top:
                    hint = (
                        "; a repo library builds on an add-on's chain by listing "
                        "<addon>@<collection> under addons: in .kraft/library.yaml"
                    )
                else:
                    hint = ""
                out.append(
                    f"{where}: {key} {value!r} reaches outside the add-on; "
                    + (reach if parent_id else f"an add-on is self-contained{hint}")
                )
            skill = component.get("skill")
            if (qualifier := _qualifier(skill)) in set(repositories) - {found.name}:
                out.append(
                    f"{where}: skill {skill!r} names repository {qualifier}'s repo library "
                    "namespace; there are no add-on dependencies"
                )
            elif qualifier in set(other_namespaces) - {found.name, parent_name, _KRAFT}:
                out.append(
                    f"{where}: skill {skill!r} names another installed Kraft add-on; "
                    + (reach if parent_id else "there are no add-on dependencies")
                )
    return out


def _removal_problems(
    found: AddonManifest,
    files: Mapping[str, tuple[str, bytes]],
    parent_id: str | None,
    ancestors: Sequence[load.InstalledAddon],
) -> list[str]:
    """Why a child's (`parent_id`) `remove:` is refused when judged with its
    `ancestors`: each name the parent's merged library lacks, which load only
    records as stale (`a-child-removes-only-what-it-inherits`). A `remove:`
    anywhere else is refused by the library itself (`remove-is-refused-
    outside-a-child`), and a library that does not build says nothing here:
    the lint after it says why."""
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    # `update` also sees these as the after-state library's `stale_removals`;
    # they are worked out here as well because validate judges a child with
    # `problems(ancestors=...)` and builds no state.
    if parent_id is None or not ancestors:
        return []
    entry = files.get("library.yaml")
    try:
        data = bounded_yaml(entry[1].decode(), [20_000]) if entry else None
    except (ValueError, yaml.YAMLError):
        return []  # `problems` says the file cannot be parsed
    if not isinstance(data, Mapping) or "remove" not in data:
        return []
    with tempfile.TemporaryDirectory() as scratch:
        root = fetch.write_scratch(Path(scratch), fetch.Extracted(dict(files)))
        child = load.InstalledAddon(
            f"{found.name}@candidate",
            found.name,
            found.name,
            found.version,
            root,
            parent=parent_id,
            parent_range=found.parent[1] if found.parent else None,
        )
        try:
            library = TemplateLibrary.from_mappings(
                {}, (), library_path=root / "local.yaml", addons=[*ancestors, child]
            )
        except TemplateLibraryError:
            return []
    own = f"{found.name}: "
    return [
        f"library.yaml: {stale.removeprefix(own)}"
        for stale in library.stale_removals
        if stale.startswith(own)
    ]


def referrers(namespace: str, addons: Sequence[load.InstalledAddon]) -> list[str]:
    """Why `namespace` may not be taken: each skill of `addons`, the other
    installed add-ons, that already names `<namespace>:`. It resolved to no
    add-on when it was installed; an add-on installed now would answer it, and
    there are no add-on dependencies. A child's reference by its parent's
    name is to its inherited layer, never to a namespace. A store that does
    not read says nothing: loading says why it is left out."""
    out: list[str] = []
    for addon in addons:
        parent_name = addon.parent.split("@", 1)[0] if addon.parent else None
        try:
            files = fetch.extract_dir(addon.root).files
        except Exception:  # noqa: BLE001 -- an edited store can break in any way
            continue
        for rel in sorted(files):
            if not (rel in ("library.yaml", "profiles.yaml") or rel.startswith("chains/")):
                continue
            try:
                data = bounded_yaml(files[rel][1].decode(), [20_000])
            except (ValueError, yaml.YAMLError):
                continue
            for at, component in _components(rel, data):
                skill = component.get("skill")
                if _qualifier(skill) == namespace != parent_name:
                    out.append(
                        f"{addon.id}: {rel}: {at or 'file'}: skill {skill!r} names this "
                        f"add-on's namespace {namespace!r}; there are no add-on dependencies"
                    )
    return out


def repo_library_dependents(addon_id: str, addons: Sequence[load.InstalledAddon]) -> list[str]:
    """One line for each loaded repo library whose `library.yaml` lists
    `addon_id` under `addons:` (`removing-a-referenced-addon-is-refused`),
    naming the repository. A store that does not read says nothing: loading
    says why."""
    out: list[str] = []
    for addon in addons:
        if addon.repo_id is None or addon.left_out is not None:
            continue
        try:
            entry = fetch.extract_dir(addon.root).files.get("library.yaml")
            data = bounded_yaml(entry[1].decode(), [20_000]) if entry else {}
        except Exception:  # noqa: BLE001 -- an edited store can break in any way
            continue
        if addon_id in load.builds_on(data):
            out.append(
                f"repository {addon.repo_id}: .kraft/library.yaml lists {addon_id} under "
                "addons:; disconnect that repository, or have it drop the line"
            )
    return out


def child_dependents(addon_id: str, addons: Sequence[load.InstalledAddon]) -> list[str]:
    """One line for each installed, enabled add-on among `addons` that names
    `addon_id` as its parent in `requires.addons`
    (`a-parent-with-children-is-not-removed`). A disabled child names no
    parent: its manifest is not read, and disabling a child is always allowed."""
    return [
        f"{addon.id} builds on it (requires.addons)"
        for addon in addons
        if addon.repo_id is None and not addon.quiet and addon.parent == addon_id
    ]


def built_on_refusal(addon_id: str, verb: str, addons: Sequence[load.InstalledAddon]) -> str | None:
    """Why `verb` (`uninstall`, `disable`) is refused for `addon_id` while an
    installed child among `addons` names it as its parent, or a repo library
    lists it under `addons:`; None when nothing does. The CLI and the
    `addons.yaml` editor both say it this way."""
    lines = child_dependents(addon_id, addons) + repo_library_dependents(addon_id, addons)
    if not lines:
        return None
    return f"cannot {verb} {addon_id}: something installed builds on it\n  " + "\n  ".join(lines)


def check(
    found: AddonManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
    named_by: Sequence[str] = (),
    repositories: Collection[str] = (),
    ancestors: Sequence[load.InstalledAddon] = (),
) -> None:
    """Raise `Refused` when this add-on's content may not be installed.
    `named_by` is what `referrers()` says of its namespace; `ancestors` are
    what `problems` takes."""
    if found_problems := [
        *problems(
            found,
            files,
            other_namespaces=other_namespaces,
            repositories=repositories,
            ancestors=ancestors,
        ),
        *named_by,
    ]:
        raise Refused(found_problems)


class Busy(Exception):
    """Add-on state could not be written: another writer holds the update
    lock, or the add-on files changed while a review was open."""


@dataclass(frozen=True)
class Result:
    """What `update` did with one add-on."""

    addon_id: str
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
def write_lock(addons_dir: Path, wait: float | None = None) -> Iterator[None]:
    """The update lock: held by every write to `addons.yaml`, the lock file
    or the store. A second writer waits `wait` seconds (`LOCK_WAIT_S` when
    None), then raises `Busy`."""
    addons_dir.mkdir(parents=True, exist_ok=True)
    with open(addons_dir / ".lock", "w") as fh:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("another add-on change is running; try again") from None
                time.sleep(0.05)
        yield


@dataclass(frozen=True)
class State:
    """What resolves in the instance with one set of add-ons loaded."""

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


def state_of(config_dir: Path, addons: Sequence[load.InstalledAddon]) -> State:
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
        config_dir, skills_dir=skills, instance_policy=policy, addons=addons
    )
    try:
        library = TemplateLibrary.from_yaml_dir(config_dir, skills_dir=skills, addons=addons)
    except TemplateLibraryError:
        library = None
    profiles: frozenset[str] = frozenset()
    table_error = None
    try:
        table = HarnessProfileTable.from_yaml(
            config_dir / "harnesses.yaml", harnesses=harness.load(None).valid, addons=addons
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


def references(config_dir: Path, repos: dict | None = None) -> Iterator[tuple[str, str, str, str]]:
    """`(where, key, kind, value)` for every qualified reference the instance's
    own files make: its library and chains, and the chain, steering and
    fallback references of `repos.yaml`, `intake.yaml`, `policy.yaml` and
    `harnesses.yaml`. `kind` is what the others must resolve as; None for a
    library reference, which lint judges. `repos` is a `repos.yaml` to read in
    place of the file: an edit not saved yet."""
    return ((w, k, kind, v) for w, k, kind, v, _ in scoped_references(config_dir, repos))


#: The files whose list items each belong to one repository, and that list:
#: a `repos.yaml` entry is its own; a schedule or trigger is its `repo`'s.
_OWNED = {"repos.yaml": "repos", "intake.yaml": "schedules", "policy.yaml": "triggers"}


@dataclass(frozen=True)
class _Entry:
    path: object
    id: object


def _repo_entries(data: object) -> list[_Entry]:
    entries = data.get("repos") if isinstance(data, Mapping) else None
    entries = entries if isinstance(entries, list) else []
    return [_Entry(e.get("path"), e.get("id")) for e in entries if isinstance(e, Mapping)]


def _owners(rel: str, data: object, entries: list[_Entry]) -> dict[str, str | None]:
    """Each owned item's key path (as `_mappings` labels it) to the id of the
    repository it belongs to."""
    section = _OWNED.get(rel)
    items = data.get(section) if section and isinstance(data, Mapping) else None
    out: dict[str, str | None] = {}
    for index, item in enumerate(items if isinstance(items, list) else ()):
        if not isinstance(item, Mapping):
            continue
        if rel == "repos.yaml":
            owner = item.get("id")
        else:
            found = connected_entry(entries, item.get("repo"))
            owner = found.id if found is not None else None
        out[f"{section}[{item.get('id', index)}]"] = owner if isinstance(owner, str) else None
    return out


def _owner(at: str, owners: Mapping[str, str | None]) -> str | None:
    for prefix, owner in owners.items():
        if at == prefix or at.startswith((f"{prefix}.", f"{prefix}[")):
            return owner
    return None


def scoped_references(
    config_dir: Path,
    repos: dict | None = None,
    *,
    replacing: Mapping[str, object] | None = None,
) -> Iterator[tuple[str, str, str | None, str, str | None]]:
    """`references()`, each with a fifth field: the id of the repository the
    reference belongs to, whose own repo library it may name. Everything under
    a `repos.yaml` entry is that entry's; a schedule's or trigger's is the
    entry its `repo` path names; anything else is no repository's (None).
    `replacing` maps a file to parsed data to read in its place: an edit not
    saved yet."""
    replacing = {**(replacing or {}), **({"repos.yaml": repos} if repos is not None else {})}
    chains = {f"chains/{p.name}" for p in config_dir.glob("chains/*.yaml")}
    chains |= {rel for rel in replacing if rel.startswith("chains/")}
    local = ["library.yaml", *sorted(chains)]

    def read(rel: str) -> object:
        return replacing[rel] if rel in replacing else read_yaml(config_dir / rel)

    try:
        entries = _repo_entries(read("repos.yaml"))
    except ConfigError:
        entries = []
    for rel in (*local, "repos.yaml", "intake.yaml", "policy.yaml", "harnesses.yaml"):
        try:
            data = read(rel)
        except ConfigError:
            continue  # that file's own check says why
        owners = _owners(rel, data, entries)
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
                    where = f"{rel}: {at}" if at else rel
                    yield where, key, kind, value, _owner(at, owners)


def scope_problems(
    config_dir: Path,
    repos: dict | None = None,
    *,
    replacing: Mapping[str, object] | None = None,
) -> list[tuple[str, str]]:
    """`(file, why)` for each reference of the instance's own files to a
    connected repository's repo library from outside that repository
    (`load.out_of_scope`). A connected id with no library counts: it is
    reserved from connect onward."""
    source = repos if repos is not None else (replacing or {}).get("repos.yaml")
    try:
        source = source if source is not None else read_yaml(config_dir / "repos.yaml")
    except ConfigError:
        return []  # that file's own check says why
    ids = set(load.connected_ids(config_dir, source if isinstance(source, Mapping) else {}))
    return [
        (where.split(":", 1)[0], f"{where}: {key} {why}")
        for where, key, _kind, value, owner in scoped_references(
            config_dir, repos, replacing=replacing
        )
        if (why := load.out_of_scope(value, owner, ids)) is not None
    ]


def breaks(
    before: State,
    after: State,
    config_dir: Path,
    namespace: str | None = None,
    *,
    exempt: Collection[str] = (),
) -> list[str]:
    """Why what resolves in `before` would not in `after`. `namespace` is the
    candidate add-on's, whose own chains must resolve too; None when no
    add-on's content changes (an `addons.yaml` edit). `exempt`: repo-library
    namespaces whose chains `held_breaks` reports instead, as Reach
    (`a-broken-repo-library-chain-holds-an-update`)."""
    out = (
        [f"harnesses.yaml: {after.table_error}"]
        if after.table_error and not before.table_error
        else []
    )
    # A chain is judged by its id: a lint message names the store it was read
    # from, so a chain that failed before fails "anew" from a candidate's
    # scratch copy. One that already failed is not this change's break.
    failed = {chain for chain, _ in before.issues if chain is not None}
    for chain, message in sorted(after.issues - before.issues, key=str):
        if chain is None:
            out.append(message)
        elif chain in failed:
            continue
        elif namespace is not None and chain.startswith(f"{namespace}:"):
            out.append(f"its chain {chain} does not resolve: {message}")
        elif _qualifier(chain) in exempt:
            continue
        elif chain in before.chains:
            out.append(f"chain {chain} would stop resolving: {message}")
    for where, key, kind, value in references(config_dir):
        if _qualifier(value) in exempt:
            continue  # a reference into a repo library breaks only through its chain
        if kind and before.resolves(kind, value) and not after.resolves(kind, value):
            out.append(f"{where}: {key} {value!r} would stop resolving")
    return out


def held_breaks(before: State, after: State, namespaces: Collection[str]) -> list[str]:
    """A Reach line for each chain of a repo library under `namespaces` that
    resolves in `before` and would not in `after`. Its repository's
    maintainers, not this instance's, can fix it, so it holds an update for a
    person and never refuses it (`a-broken-repo-library-chain-holds-an-update`)."""
    out = []
    for chain, message in sorted(after.issues - before.issues, key=str):
        qualifier = _qualifier(chain)
        if qualifier in namespaces and chain in before.chains:
            out.append(
                f"chain {chain} would stop resolving: {message}; {qualifier}'s items "
                "refuse it until its repo library is fixed"
            )
    return out


def _chains_of(namespaces: Collection[str], state: State) -> set[str]:
    """Every chain of a repo library under `namespaces` that resolves in
    `state`. One that stops resolving is a `held_breaks` line, not a
    "removed" line for every fact it had."""
    return {chain for chain in state.chains if _qualifier(chain) in namespaces}


def _catalogue(
    name: str, collection: CollectionConfig, addons_dir: Path
) -> tuple[Path | None, str | None, manifest.CollectionManifest]:
    """The collection fetched at its ref: its mirror and commit (None for a
    directory) and its `collection.json`."""
    where = manifest.COLLECTION_JSON
    if collection.git is not None:
        mirror, commit = fetch.fetch(addons_dir, name, collection.git, collection.ref)
        data = fetch.read_file(mirror, commit, where, fetch.MAX_JSON)
        source = fetch.redact(collection.git)
    else:
        mirror, commit, source = None, None, str(collection.path)
        data = fetch.read_regular(Path(source) / where, fetch.MAX_JSON)
    try:
        text = data.decode()
    except UnicodeDecodeError as exc:
        raise fetch.AddonRefused(f"{source}: {where} is not valid UTF-8") from exc
    listed = manifest.collection(manifest.parse(text, where), where)
    if listed.name != name:
        raise fetch.AddonRefused(
            f"collection at {source} now calls itself {listed.name}; re-add it under that name"
        )
    return mirror, commit, listed


def _ancestors(
    candidate: load.InstalledAddon, addons: Sequence[load.InstalledAddon], config: AddonsConfig
) -> tuple[load.InstalledAddon, ...]:
    """`candidate`'s ancestors among `addons` (the rest of the instance),
    parent first; empty for a root add-on. Raises `Refused` when its ancestry
    does not hold, with what to run: install the parent, add its collection,
    or take another release (`install-refuses-a-missing-parent-collection`,
    `every-child-accepts-its-parents-version`). Slice 1 installs no ancestor
    itself."""
    parent_id = candidate.parent
    if parent_id is None:
        return ()
    parent = next((a for a in addons if a.id == parent_id and a.repo_id is None), None)
    if parent is None:
        collection = parent_id.split("@", 1)[1]
        if collection not in config.collections:
            raise Refused(
                [
                    f"its parent {parent_id} comes from collection {collection}, which this "
                    "instance has not added; add it first: kraft admin addon collection add "
                    "<its git URL or directory>"
                ]
            )
        raise Refused(
            [
                f"its parent {parent_id} is not installed; install it first: "
                f"kraft admin addon install {parent_id}"
            ]
        )
    try:
        return inherit.ancestry(candidate, addons)[1:]
    except inherit.InheritanceError as exc:
        hint = ""
        if parent.left_out == load.DISABLED:
            hint = f"; enable it first: kraft admin addon enable {parent_id}"
        elif parent.left_out is None and _outside(candidate.parent_range, parent.version):
            hint = (
                f"; point collection {parent_id.split('@', 1)[1]}'s ref at a release it "
                f"accepts, or wait for {candidate.id} to accept {parent.version}"
            )
        raise Refused([f"{exc}{hint}"]) from exc


def _outside(range_: str | None, version: str) -> str | None:
    """Why `version` is outside `range_`; None when it is inside, or there is
    no range. A version that does not read is outside."""
    if range_ is None:
        return None
    try:
        return manifest.version_in_range(range_, version)
    except ValueError as exc:
        return str(exc)


def _as_installed(
    addon_id: str, namespace: str, found: AddonManifest, root: Path
) -> load.InstalledAddon:
    """A candidate as the add-on it would load as, its parent from `found`."""
    parent, range_ = found.parent or (None, None)
    name = addon_id.split("@", 1)[0]
    return load.InstalledAddon(
        addon_id, name, namespace, found.version, root, parent=parent, parent_range=range_
    )


def _children_left_behind(
    addon_id: str, version: str, addons: Sequence[load.InstalledAddon]
) -> list[tuple[str, str]]:
    """`(child id, why)` for each loading child among `addons` whose range
    `version` of its parent `addon_id` falls outside: moving the parent would
    leave it out. Slice 1 refuses the move; it does not move the set, so the
    way across is to take the child out first."""
    return [
        (
            child.id,
            f"{version} would leave {child.id} outside its range {child.parent_range} ({why}); "
            f"it would no longer load. Disable {child.id} first, then update it to a "
            f"release that accepts {version} and enable it",
        )
        for child in addons
        if child.repo_id is None
        and child.left_out is None
        and child.parent == addon_id
        and (why := _outside(child.parent_range, version))
    ]


@dataclass(frozen=True)
class _Candidate:
    addon_id: str
    extracted: fetch.Extracted
    mirror: Path | None
    locks_as: LockEntry


def update(
    ids: Sequence[str] | None = None,
    *,
    accept: Callable[[Review], bool],
    re_install: bool = False,
    entries: Mapping[str, AddonEntry | bool] | None = None,
    config_dir: Path | None = None,
    addons_root: Path | None = None,
) -> list[Result]:
    """Install or update `ids` (every add-on in `addons.yaml` when None), one
    `Result` each. `accept` is asked once per add-on that has something to
    take, with its review. `entries` are new or changed `addons.yaml` entries
    (an install, an alias), held in memory and written only for an add-on that
    was accepted. `re_install` takes the commit the ref resolves to now even
    at an unchanged version. Raises `Refused` when `entries` do not fit
    `addons.yaml`; `ConfigError` or `PolicyError` when `addons.yaml`, the lock
    or `policy.yaml` cannot be read."""
    from kraft import paths
    from kraft import update as kraft_update
    from kraft.addons import review as review_mod

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    addons_root = Path(addons_root) if addons_root is not None else load.addons_dir()
    files = (config_dir / AddonsConfig.FILE, config_dir / AddonsLock.FILE)
    seen = [f.read_bytes() if f.exists() else None for f in files]
    written = read_yaml(files[0], {})
    lock = AddonsLock.load(files[1])
    new_entries = {
        addon_id: e
        if isinstance(e, bool)
        else e.model_dump(by_alias=True, exclude_defaults=True) or True
        for addon_id, e in (entries or {}).items()
    }
    try:
        config = AddonsConfig.model_validate(
            {**written, "addons": {**(written.get("addons") or {}), **new_entries}}
        )
    except ValidationError as exc:
        raise Refused([first_error(exc, AddonsConfig.FILE)]) from exc

    running = kraft_update.installed()
    catalogues: dict[str, tuple | Exception] = {}
    results: dict[str, Result] = {}
    accepted: list[_Candidate] = []
    with tempfile.TemporaryDirectory() as scratch:
        loaded = list(load.installed(config_dir, addons_root))
        state = state_of(config_dir, loaded)
        for addon_id in ids if ids is not None else list(config.addons):
            if addon_id not in config.addons:
                results[addon_id] = Result(
                    addon_id, "refused", problems=("is not in addons.yaml; install it first",)
                )
                continue
            name, collection_name = addon_id.split("@", 1)
            collection = config.collections[collection_name]
            locked = lock.addons.get(addon_id)
            namespace = config.namespace(addon_id)
            try:
                if collection_name not in catalogues:
                    try:
                        catalogues[collection_name] = _catalogue(
                            collection_name, collection, addons_root
                        )
                    except (fetch.FetchError, fetch.AddonRefused, manifest.ManifestError) as exc:
                        catalogues[collection_name] = exc
                if isinstance(catalogues[collection_name], Exception):
                    raise catalogues[collection_name]
                mirror, commit, listed = catalogues[collection_name]
                entry = next((e for e in listed.addons if e.name == name), None)
                if entry is None and locked is not None:
                    results[addon_id] = Result(
                        addon_id,
                        "unpublished",
                        problems=(
                            f"is no longer published by {collection_name}; "
                            f"it stays on {locked.version}",
                        ),
                    )
                    continue
                if entry is None:
                    raise fetch.AddonRefused(f"collection {collection_name} has no add-on {name}")
                if mirror is not None:
                    extracted = fetch.extract_git(mirror, commit, entry.source)
                else:
                    extracted = fetch.extract_dir(
                        Path(collection.path) / entry.source.removeprefix("./")
                    )
                if manifest.ADDON_JSON not in extracted.files:
                    raise manifest.ManifestError(f"no {manifest.ADDON_JSON}")
                found = manifest.addon(
                    manifest.parse(
                        extracted.files[manifest.ADDON_JSON][1].decode(), manifest.ADDON_JSON
                    ),
                    manifest.ADDON_JSON,
                )
                manifest.check_addon(found, name, extracted.files)
                if not manifest.is_source_build(running):
                    if why := manifest.kraft_compatible(found.requires.kraft, running):
                        raise Refused([f"{found.version} {why}; this is Kraft {running}"])
                holders = {
                    held.namespace: other
                    for other, held in lock.addons.items()
                    if other != addon_id
                }
                if namespace in holders:
                    raise Refused(
                        [
                            f"namespace {namespace!r} is still held by {holders[namespace]}; "
                            "update that add-on first"
                        ]
                    )
                if why := load.instance_problem(namespace, found.requires, config_dir):
                    raise Refused([why])
                rest = [p for p in loaded if p.id != addon_id]
                # The candidate as the tree sees it; its root is swapped for the
                # scratch copy once it is known to be pending. Neither the
                # ancestry nor the cascade reads a root.
                candidate = _as_installed(addon_id, namespace, found, Path(scratch))
                # Why the move would not hold in this tree: said only if there
                # is a move, so a current add-on whose parent is left out stays
                # current rather than refused on every routine update.
                held_by_tree: list[str] = []
                try:
                    ancestors = _ancestors(candidate, rest, config)
                except Refused as exc:
                    ancestors, held_by_tree = (), list(exc.problems)
                would_load = list(inherit.cascade([*rest, candidate]))
                behind = _children_left_behind(addon_id, found.version, rest)
                held_by_tree += [why for _child, why in behind]
                # A descendant whose ancestry no longer holds is named: its
                # chains would be gone, not broken.
                loading = {p.id for p in rest if p.left_out is None}
                held_by_tree += [
                    f"{p.id} would no longer load: {p.left_out}"
                    for p in would_load
                    if p.id in loading
                    and p.left_out is not None
                    and p.id not in {child for child, _why in behind}
                ]
                others = (
                    set(holders)
                    | {config.namespace(o) for o in config.addons if o != addon_id}
                    | {p.namespace for p in loaded if p.repo_id is not None}
                )
                check(
                    found,
                    extracted.files,
                    other_namespaces=others,
                    named_by=referrers(namespace, rest),
                    repositories=load.connected_ids(config_dir),
                    ancestors=ancestors,
                )
            except fetch.FetchError as exc:
                results[addon_id] = Result(addon_id, "failed", problems=(str(exc),), kind=exc.kind)
                continue
            except Refused as exc:
                results[addon_id] = Result(addon_id, "refused", problems=tuple(exc.problems))
                continue
            except (fetch.AddonRefused, manifest.ManifestError, OSError) as exc:
                results[addon_id] = Result(addon_id, "refused", problems=(str(exc),))
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
                notes = list(held_by_tree)
                if extracted.tree != locked.tree:
                    notes.append(
                        f"version {locked.version} unchanged, but its files changed at "
                        f"{commit[:12]}; --re-install to take it"
                    )
                results[addon_id] = Result(addon_id, "current", note="; ".join(notes) or None)
                continue
            if held_by_tree:
                results[addon_id] = Result(addon_id, "refused", problems=tuple(held_by_tree))
                continue

            store = (
                addons_root / "store" / locked.digest.removeprefix("sha256:") if locked else None
            )
            old = reviewed = None
            if store is not None and store.is_dir():
                try:
                    old = fetch.verify_store(store)
                    reviewed = review_mod.review(addon_id, old, extracted)
                except Exception:  # noqa: BLE001 -- an edited store can break in any way
                    old = None
            if old is None:
                # Nothing installed, or a store that is gone or does not read:
                # everything in the candidate is shown as new.
                reviewed = review_mod.review(addon_id, None, extracted)
            if old is None and locked is not None:
                # Still an update, and one nobody can compare: said first, and held.
                said = [f"the installed {locked.version} could not be read to compare with"]
                # The lock still knows the version: a step back is said as ever.
                if review_mod.precedence(found.version) < review_mod.precedence(locked.version):
                    said.append(f"downgrade: {locked.version} -> {found.version}")
                reviewed = replace(
                    reviewed, old_version=locked.version, reach=(*said, *reviewed.reach)
                )
            if locked is not None and _moved(collection, locked):
                was = fetch.redact(locked.git) if locked.git else "a local directory"
                now = fetch.redact(collection.git) if collection.git else "a local directory"
                moved = f"collection URL changed: {was} -> {now}"
                reviewed = replace(reviewed, reach=(moved, *reviewed.reach))

            root = fetch.write_scratch(Path(scratch), extracted)
            would_load = [replace(p, root=root) if p.id == addon_id else p for p in would_load]
            after = state_of(config_dir, would_load)
            libraries = {p.namespace for p in would_load if p.repo_id is not None}
            if broken := breaks(state, after, config_dir, namespace, exempt=libraries):
                results[addon_id] = Result(
                    addon_id,
                    "refused",
                    review=reviewed,
                    problems=tuple(why.replace(str(root), addon_id) for why in broken),
                )
                continue
            held = held_breaks(state, after, libraries)
            captured = (
                [
                    f"{where}: {key} {value!r} is now read from this add-on"
                    for where, key, _kind, value in references(config_dir)
                    if _qualifier(value) == namespace
                ]
                if locked is None or locked.namespace != namespace
                else []
            )
            reach, content = (
                review_mod.local_changes(
                    state.library,
                    after.library,
                    namespace,
                    extra=_chains_of(libraries, after),
                )
                if after.library is not None
                else ([], [])
            )
            reviewed = replace(
                reviewed,
                reach=(*held, *reviewed.reach, *captured, *reach),
                content=(*reviewed.content, *content),
            )
            if not accept(reviewed):
                results[addon_id] = Result(addon_id, "declined", review=reviewed)
                continue
            loaded, state = would_load, after
            results[addon_id] = Result(addon_id, "applied", review=reviewed)
            accepted.append(
                _Candidate(
                    addon_id,
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
            with write_lock(addons_root):
                if [f.read_bytes() if f.exists() else None for f in files] != seen:
                    raise Busy("add-ons changed while you reviewed; run update again")
                for done in accepted:
                    fetch.write_store(addons_root, done.extracted)
                    if done.mirror is not None:
                        fetch.pin(done.mirror, done.locks_as.commit)
                took = {
                    d.addon_id: new_entries[d.addon_id]
                    for d in accepted
                    if d.addon_id in new_entries
                }
                if took:
                    # addons.yaml first: a crash before the lock leaves a
                    # known "listed, not installed" entry.
                    write_yaml(
                        files[0], {**written, "addons": {**(written.get("addons") or {}), **took}}
                    )
                # A lock entry `addons.yaml` no longer lists is dropped here.
                kept = {i: e for i, e in lock.addons.items() if i in config.addons}
                locks = AddonsLock(addons=kept | {d.addon_id: d.locks_as for d in accepted})
                write_text(
                    files[1],
                    _LOCK_HEADER + yaml.safe_dump(locks.model_dump(mode="json"), sort_keys=False),
                )
        except Busy as exc:
            for done in accepted:
                results[done.addon_id] = replace(
                    results[done.addon_id], outcome="failed", problems=(str(exc),), kind="busy"
                )
        else:
            # `auto_update` records its own outcome after this.
            clear_status(addons_root, {done.addon_id for done in accepted})
    return list(results.values())


#: Where each add-on's last auto-update is recorded, under `run/addons/`:
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


def clear_status(addons_root: Path, addon_ids: Collection[str]) -> None:
    """Forget the last auto-update outcome of `addon_ids`: a person has just
    acted on them (applied the update, uninstalled the add-on), so "held" or
    "failed" no longer says anything true."""
    import json

    status = read_status(addons_root)
    left = {addon_id: entry for addon_id, entry in status.items() if addon_id not in addon_ids}
    if left != status:
        write_text(Path(addons_root) / STATUS_FILE, json.dumps(left, indent=2) + "\n")


def read_status(addons_root: Path) -> dict[str, dict]:
    """Add-on id to its last auto-update `{at, outcome, kind, message}`; empty
    when none has run or the file does not read."""
    import json

    try:
        data = json.loads((Path(addons_root) / STATUS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def auto_update(
    config_dir: Path | None = None,
    addons_root: Path | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, dict]:
    """Update every locked add-on whose auto-update is on (its collection's, or
    its own), applying only what `may_apply_unattended` allows, and record
    each one's outcome in `status.json`: `applied`, `up to date`, `held` or
    `failed` (with `kind`: `network`, `auth`, `refused` or `busy`). Never
    installs, never re-installs, and never applies a ref or alias changed by
    hand: those wait for a person's `update`. A collection none of whose
    add-ons auto-update is not fetched. `cancel` (set at shutdown) is checked
    before each collection: the rest are reported `cancelled`, and not
    recorded, so a restart does not read a stop as a failure."""
    import json

    from kraft import paths

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    addons_root = Path(addons_root) if addons_root is not None else load.addons_dir()
    try:
        config = AddonsConfig.load(config_dir / AddonsConfig.FILE)
        lock = AddonsLock.load(config_dir / AddonsLock.FILE)
    except ConfigError:
        return {}  # the load says why
    outcomes: dict[str, dict] = {}
    ids = []
    for addon_id, locked in lock.addons.items():
        if addon_id not in config.addons:
            continue
        collection = config.collections[addon_id.split("@", 1)[1]]
        if not (collection.auto_update or config.entry(addon_id).auto_update):
            continue
        pending = [
            what
            for what, changed in (
                ("ref", collection.ref != locked.ref),
                ("collection URL", _moved(collection, locked)),
                ("alias", config.namespace(addon_id) != locked.namespace),
            )
            if changed
        ]
        if pending:
            outcomes[addon_id] = {
                "outcome": "held",
                "message": f"{pending[0]} change pending; run kraft admin addon update",
            }
        else:
            ids.append(addon_id)
    held: dict[str, str] = {}

    def accept(review: Review) -> bool:
        why = may_apply_unattended(review)
        if why is not None:
            held[review.addon_id] = why
        return why is None

    by_collection: dict[str, list[str]] = {}
    for addon_id in ids:
        by_collection.setdefault(addon_id.split("@", 1)[1], []).append(addon_id)
    results: list[Result] = []
    for collection_ids in by_collection.values():
        if cancel is not None and cancel.is_set():
            for addon_id in collection_ids:
                outcomes[addon_id] = {
                    "outcome": "failed",
                    "kind": "cancelled",
                    "message": "stopped at shutdown",
                }
            continue
        results += update(
            collection_ids, accept=accept, config_dir=config_dir, addons_root=addons_root
        )
    for result in results:
        said = "; ".join(result.problems)
        outcomes[result.addon_id] = {
            "applied": {"outcome": "applied"},
            "current": {"outcome": "up to date"},
            "unpublished": {"outcome": "up to date", "message": said},
            "declined": {
                "outcome": "held",
                "message": f"{held.get(result.addon_id)}; run kraft admin addon update",
            },
            "refused": {"outcome": "failed", "kind": "refused", "message": said},
            "failed": {"outcome": "failed", "kind": result.kind, "message": said},
        }[result.outcome]
    recorded = {i: o for i, o in outcomes.items() if o.get("kind") != "cancelled"}
    if recorded:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        status = read_status(addons_root) | {
            addon_id: {"at": now, "kind": None, "message": None, **outcome}
            for addon_id, outcome in recorded.items()
        }
        addons_root.mkdir(parents=True, exist_ok=True)
        write_text(addons_root / STATUS_FILE, json.dumps(status, indent=2) + "\n")
    return outcomes
