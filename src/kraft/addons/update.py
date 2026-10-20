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
from typing import TYPE_CHECKING, NamedTuple

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
    from kraft.addons.review import Group, Review
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
    from kraft.templates.library import ADDON_YAML_NODES, TemplateLibrary, TemplateLibraryError

    # Worked out from the candidate and its ancestors, not from any state:
    # validate judges a child with `problems(ancestors=...)` and builds none.
    if parent_id is None or not ancestors:
        return []
    entry = files.get("library.yaml")
    try:
        data = bounded_yaml(entry[1].decode(), [ADDON_YAML_NODES]) if entry else None
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
    #: accepted: an update is waiting); `held` (a range drops the move from
    #: the set: it waits on its locked version); `refused` (a check failed);
    #: `unpublished` (gone from its collection, kept on its lock); `failed`
    #: (could not be checked or written).
    outcome: str
    review: Review | None = None
    #: Why, for `held`, `refused`, `unpublished` and `failed`.
    problems: tuple[str, ...] = ()
    #: For `failed`: `network`, `auth`, `refused` or `busy`. For `held`:
    #: `breaks` when a relative's own move breaks a chain; None for a range.
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
    namespace: str | Collection[str] | None = None,
    *,
    exempt: Collection[str] = (),
) -> list[str]:
    """Why what resolves in `before` would not in `after`. `namespace` is the
    candidate add-on's, or every namespace of a tree moving together, whose
    own chains must resolve too; None when no add-on's content changes (an
    `addons.yaml` edit). `exempt`: repo-library
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
    own = {namespace} if isinstance(namespace, str) else set(namespace or ())
    for chain, message in sorted(after.issues - before.issues, key=str):
        if chain is None:
            out.append(message)
        elif chain in failed:
            continue
        elif _qualifier(chain) in own:
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


def catalogue(
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


def _missing_collection(parent_id: str) -> str:
    """Why an add-on whose parent `parent_id` comes from a collection this
    instance has not added is refused, and what to run."""
    collection = parent_id.split("@", 1)[1]
    return (
        f"its parent {parent_id} comes from collection {collection}, which this instance has "
        "not added; add it first: kraft admin addon collection add <its git URL or directory>"
    )


def _major(version: str) -> int | None:
    """The major part of `version` or a range; None when it does not read."""
    try:
        return int(version.partition(".")[0])
    except ValueError:
        return None


def _ancestors(
    candidate: load.InstalledAddon,
    addons: Sequence[load.InstalledAddon],
    config: AddonsConfig,
    locked: Collection[str],
) -> tuple[load.InstalledAddon, ...]:
    """`candidate`'s ancestors among `addons` (the rest of the instance),
    parent first; empty for a root add-on. Raises `Refused` when its ancestry
    does not hold, with what to run: install the parent, add its collection,
    or take another release (`install-refuses-a-missing-parent-collection`,
    `every-child-accepts-its-parents-version`). A parent not among `locked`
    (ids) is being installed with it: one newer than the range allows is
    said from the parent's side, with both ways out."""
    parent_id = candidate.parent
    if parent_id is None:
        return ()
    parent = next((a for a in addons if a.id == parent_id and a.repo_id is None), None)
    if parent is None:
        if parent_id.split("@", 1)[1] not in config.collections:
            raise Refused([_missing_collection(parent_id)])
        raise Refused(
            [
                f"its parent {parent_id} is not installed; install it first: "
                f"kraft admin addon install {parent_id}"
            ]
        )
    try:
        return inherit.ancestry(candidate, addons)[1:]
    except inherit.InheritanceError as exc:
        collection = parent_id.split("@", 1)[1]
        why = _outside(candidate.parent_range, parent.version) if parent.left_out is None else None
        wants, major = _major(candidate.parent_range or ""), _major(parent.version)
        if why and parent_id not in locked and wants is not None and (major or 0) > wants:
            raise Refused(
                [
                    f"{parent_id} {parent.version} is outside {candidate.id}'s range "
                    f"{candidate.parent_range} ({why}); point collection {collection}'s ref at "
                    f"a {wants}.x tag, or wait for {candidate.id} to accept {major}"
                ]
            ) from exc
        hint = ""
        if parent.left_out == load.DISABLED:
            hint = f"; enable it first: kraft admin addon enable {parent_id}"
        elif why:
            hint = (
                f"; point collection {collection}'s ref at a release it "
                f"accepts, or wait for {candidate.id} to accept {parent.version}"
            )
        raise Refused([f"{exc}{hint}"]) from exc


def _ancestry_in(
    addon_id: str, addons: Sequence[load.InstalledAddon]
) -> tuple[load.InstalledAddon, ...]:
    """`addon_id`'s ancestors among `addons`, parent first; empty for a root
    add-on, one not among them, or one whose ancestry does not hold there."""
    addon = next((a for a in addons if a.id == addon_id and a.repo_id is None), None)
    if addon is None or addon.parent is None:
        return ()
    try:
        return inherit.ancestry(addon, addons)[1:]
    except inherit.InheritanceError:
        return ()


def _descendants(addon_id: str, addons: Sequence[load.InstalledAddon]) -> set[str]:
    """The namespaces of the add-ons among `addons` built on `addon_id`, at any
    depth; one whose ancestry does not hold there is not among them."""
    return {
        a.namespace
        for a in addons
        if a.repo_id is None and addon_id in {p.id for p in _ancestry_in(a.id, addons)}
    }


def _outside(range_: str | None, version: str) -> str | None:
    """Why `version` is outside `range_`; None when it is inside, or there is
    no range. A version that does not read is outside."""
    if range_ is None:
        return None
    try:
        return manifest.version_in_range(range_, version)
    except ValueError as exc:
        return str(exc)


def as_installed(
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
    `version` of its parent `addon_id` falls outside: it would not load. Said
    of a current add-on only; a move a range rejects is the set rule's, which
    holds it (`_settle`)."""
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
class _Move:
    """One add-on going from its locked version (none for an install) to its
    collection's candidate, resolved and written to a scratch root."""

    addon_id: str
    extracted: fetch.Extracted
    mirror: Path | None
    name: str
    namespace: str
    found: AddonManifest
    commit: str | None
    source: str
    digest: str
    collection: CollectionConfig
    locked: LockEntry | None
    #: The locked store's files; None when nothing is installed or it does not read.
    old: fetch.Extracted | None
    #: Said first in its review: the collection URL that moved, and an
    #: installed version that could not be read (`_said`).
    said: tuple[str, ...]
    #: The candidate as it would load, from its scratch root.
    candidate: load.InstalledAddon

    def locks_as(self) -> LockEntry:
        return LockEntry(
            namespace=self.namespace,
            ref=self.collection.ref,
            git=self.collection.git,
            commit=self.commit,
            tree=self.extracted.tree,
            source=self.source,
            digest=self.digest,
            version=self.found.version,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class _Context:
    """What every move of one `update` is resolved and checked against."""

    config: AddonsConfig
    lock: AddonsLock
    #: The add-ons installed when the update started.
    loaded: Sequence[load.InstalledAddon]
    running: str
    #: Collection name to its `catalogue`, or the error fetching it gave.
    catalogues: dict[str, tuple | Exception]
    config_dir: Path
    addons_root: Path
    scratch: Path
    re_install: bool


class _Judged(NamedTuple):
    """The instance with a tree's moves applied, and what they break in it."""

    would_load: list[load.InstalledAddon]
    after: State
    #: The namespaces of the repo libraries loading in it.
    libraries: set[str]
    broken: list[str]


def _said(
    locked: LockEntry | None,
    collection: CollectionConfig,
    found: AddonManifest,
    old: fetch.Extracted | None,
) -> tuple[str, ...]:
    """What a review says before its own lines: a collection URL that moved,
    and an update nobody can compare, which holds it."""
    from kraft.addons import review as review_mod

    out = []
    if locked is not None and _moved(collection, locked):
        was = fetch.redact(locked.git) if locked.git else "a local directory"
        now = fetch.redact(collection.git) if collection.git else "a local directory"
        out.append(f"collection URL changed: {was} -> {now}")
    if old is None and locked is not None:
        out.append(f"the installed {locked.version} could not be read to compare with")
        # The lock still knows the version: a step back is said as ever.
        if review_mod.precedence(found.version) < review_mod.precedence(locked.version):
            out.append(f"downgrade: {locked.version} -> {found.version}")
    return tuple(out)


def _judge(
    found: AddonManifest,
    files: Mapping[str, tuple[str, bytes]],
    candidate: load.InstalledAddon,
    rest: Sequence[load.InstalledAddon],
    ctx: _Context,
    manifests: Mapping[str, AddonManifest] | None = None,
) -> list[str]:
    """Raise `Refused` when `candidate` may not be taken among `rest` (the
    instance as it would be around it): another add-on holds its namespace,
    the instance lacks what it and its ancestors require, or its content is
    refused. Returns why its ancestry does not hold there, which refuses a
    move and is a note on a current add-on."""
    addon_id, namespace = candidate.id, candidate.namespace
    holders = {
        held.namespace: other for other, held in ctx.lock.addons.items() if other != addon_id
    }
    if namespace in holders:
        raise Refused(
            [
                f"namespace {namespace!r} is still held by {holders[namespace]}; "
                "update that add-on first"
            ]
        )
    held_by_tree: list[str] = []
    try:
        ancestors = _ancestors(candidate, rest, ctx.config, ctx.lock.addons)
    except Refused as exc:
        ancestors, held_by_tree = (), list(exc.problems)
    try:
        requires = inherit.requirements(
            (candidate, *ancestors), {**(manifests or {}), addon_id: found}
        )
    except (fetch.AddonRefused, fetch.FetchError, manifest.ManifestError, OSError, ValueError):
        requires = found.requires  # an ancestor's store does not read; loading says why
    if why := load.instance_problem(namespace, requires, ctx.config_dir):
        raise Refused([why])
    others = (
        set(holders)
        | {ctx.config.namespace(o) for o in ctx.config.addons if o != addon_id}
        | {p.namespace for p in ctx.loaded if p.repo_id is not None}
    )
    check(
        found,
        files,
        other_namespaces=others,
        named_by=referrers(namespace, rest),
        repositories=load.connected_ids(ctx.config_dir),
        ancestors=ancestors,
    )
    return held_by_tree


def _dropped(
    would_load: Sequence[load.InstalledAddon], loading: Collection[str], but: Collection[str] = ()
) -> list[str]:
    """A line for each of `loading` (ids) that `would_load` leaves out, `but`
    those said otherwise: its chains would be gone, not broken."""
    return [
        f"{p.id} would no longer load: {p.left_out}"
        for p in would_load
        if p.id in loading and p.left_out is not None and p.id not in but
    ]


def _resolve(addon_id: str, ctx: _Context) -> _Move | Result:
    """`addon_id`'s move, read from its collection; else what `update` says of
    it: `current` with nothing to take (judged against the instance as it is),
    or `refused`, `failed` or `unpublished`."""
    if addon_id not in ctx.config.addons:
        return Result(addon_id, "refused", problems=("is not in addons.yaml; install it first",))
    name, collection_name = addon_id.split("@", 1)
    collection = ctx.config.collections[collection_name]
    locked = ctx.lock.addons.get(addon_id)
    namespace = ctx.config.namespace(addon_id)
    catalogues = ctx.catalogues
    try:
        if collection_name not in catalogues:
            try:
                catalogues[collection_name] = catalogue(
                    collection_name, collection, ctx.addons_root
                )
            except (fetch.FetchError, fetch.AddonRefused, manifest.ManifestError) as exc:
                catalogues[collection_name] = exc
        if isinstance(catalogues[collection_name], Exception):
            raise catalogues[collection_name]
        mirror, commit, listed = catalogues[collection_name]
        entry = next((e for e in listed.addons if e.name == name), None)
        if entry is None and locked is not None:
            return Result(
                addon_id,
                "unpublished",
                problems=(
                    f"is no longer published by {collection_name}; it stays on {locked.version}",
                ),
            )
        if entry is None:
            raise fetch.AddonRefused(f"collection {collection_name} has no add-on {name}")
        if mirror is not None:
            extracted = fetch.extract_git(mirror, commit, entry.source)
        else:
            extracted = fetch.extract_dir(Path(collection.path) / entry.source.removeprefix("./"))
        if manifest.ADDON_JSON not in extracted.files:
            raise manifest.ManifestError(f"no {manifest.ADDON_JSON}")
        found = manifest.addon(
            manifest.parse(extracted.files[manifest.ADDON_JSON][1].decode(), manifest.ADDON_JSON),
            manifest.ADDON_JSON,
        )
        manifest.check_addon(found, name, extracted.files)
        if not manifest.is_source_build(ctx.running):
            if why := manifest.kraft_compatible(found.requires.kraft, ctx.running):
                raise Refused([f"{found.version} {why}; this is Kraft {ctx.running}"])
        digest = fetch.digest(extracted.files)
        pending = (
            locked is None
            or ctx.re_install
            or found.version != locked.version
            or collection.ref != locked.ref
            or _moved(collection, locked)
            or namespace != locked.namespace
            or (mirror is None and digest != locked.digest)
        )
        if not pending:
            # Judged against the instance as it is. Why its ancestry would not
            # hold is a note, so a current add-on whose parent is left out stays
            # current rather than refused on every routine update.
            rest = [p for p in ctx.loaded if p.id != addon_id]
            candidate = as_installed(addon_id, namespace, found, ctx.scratch)
            held_by_tree = _judge(found, extracted.files, candidate, rest, ctx)
            behind = _children_left_behind(addon_id, found.version, rest)
            held_by_tree += [why for _child, why in behind]
            held_by_tree += _dropped(
                inherit.cascade([*rest, candidate]),
                {p.id for p in rest if p.left_out is None},
                {child for child, _why in behind},
            )
            if extracted.tree != locked.tree:
                held_by_tree.append(
                    f"version {locked.version} unchanged, but its files changed at "
                    f"{commit[:12]}; --re-install to take it"
                )
            return Result(addon_id, "current", note="; ".join(held_by_tree) or None)
    except fetch.FetchError as exc:
        return Result(addon_id, "failed", problems=(str(exc),), kind=exc.kind)
    except Refused as exc:
        return Result(addon_id, "refused", problems=tuple(exc.problems))
    except (fetch.AddonRefused, manifest.ManifestError, OSError) as exc:
        return Result(addon_id, "refused", problems=(str(exc),))

    store = ctx.addons_root / "store" / locked.digest.removeprefix("sha256:") if locked else None
    old = None
    if store is not None and store.is_dir():
        try:
            old = fetch.verify_store(store)
        except Exception:  # noqa: BLE001 -- an edited store can break in any way
            old = None
    root = fetch.write_scratch(ctx.scratch, extracted)
    return _Move(
        addon_id,
        extracted,
        mirror,
        name=name,
        namespace=namespace,
        found=found,
        commit=commit,
        source=entry.source,
        digest=digest,
        collection=collection,
        locked=locked,
        old=old,
        said=_said(locked, collection, found, old),
        candidate=as_installed(addon_id, namespace, found, root),
    )


def _choose(
    moves: dict[str, _Move],
    ctx: _Context,
    builders: Mapping[str, Sequence[str]] | None = None,
    gone: Mapping[str, Result] | None = None,
) -> dict[str, Result]:
    """Phases 2 and 3 together: which of `moves` apply. The set rule settles
    the moves not refused yet, each is judged against the ones it keeps
    (`_check_moves`), and a refusal settles and judges again, until none is
    refused. A dropped move is taken out of `moves`, and its `Result`
    returned: a refusal stands over a hold. `builders` maps each ancestor an
    install pulled in, or a re-install needs, to the children it is taken
    for, and `gone` holds what was said of the installs refused before; what
    either leaves alone is dropped with them (`_orphans`)."""
    builders = builders or {}
    refused: dict[str, Result] = {}
    while True:
        refused |= _orphans(moves, {**(gone or {}), **refused}, builders, ctx)
        kept = dict(moves)
        held = _settle(kept, ctx)
        failing = _check_moves(moves, kept, ctx)
        if not failing:
            # An ancestor taken for children the set rule dropped would move
            # alone. (A range-dropped install is refused by `_check_moves`
            # first; a re-installed child the set rule holds is not.)
            failing = _orphans(dict(kept), held, builders, ctx)
            if not failing:
                break
        for addon_id in failing:
            del moves[addon_id]
        refused |= failing
    for addon_id in held:
        del moves[addon_id]
    return refused | held


def _orphans(
    moves: dict[str, _Move],
    gone: Mapping[str, Result],
    builders: Mapping[str, Sequence[str]],
    ctx: _Context,
) -> dict[str, Result]:
    """The moves that `gone` (results of moves not taken) leaves without
    their tree, to a fixed point, each taken out of `moves`: an install whose
    parent is an install in `gone`, refused, and an ancestor in `builders`
    none of whose children is left: refused when pulled in, held when a
    re-install needed it. Nothing of a half tree is written."""
    out: dict[str, Result] = {}
    while True:
        said = {**gone, **out}
        found: dict[str, Result] = {}
        for addon_id, move in moves.items():
            parent_id = move.candidate.parent
            if move.locked is None and parent_id in said and parent_id not in ctx.lock.addons:
                why = "; ".join(said[parent_id].problems)
                found[addon_id] = Result(
                    addon_id,
                    "refused",
                    problems=(f"its parent {parent_id} cannot be installed: {why}",),
                )
            elif addon_id in builders and not moves.keys() & set(builders[addon_id]):
                children = ", ".join(builders[addon_id])
                found[addon_id] = (
                    Result(
                        addon_id,
                        "refused",
                        problems=(f"pulled in for {children}, which cannot be installed",),
                    )
                    if move.locked is None
                    else Result(
                        addon_id,
                        "held",
                        problems=(f"needed by {children}, which cannot be re-installed",),
                    )
                )
        if not found:
            return out
        for addon_id in found:
            del moves[addon_id]
        out |= found


def _check_moves(
    moves: Mapping[str, _Move], kept: Collection[str], ctx: _Context
) -> dict[str, Result]:
    """Phase 2: why each of `moves` is refused. A move is judged in the
    instance as loaded, with its own candidate and those of its ancestors'
    moves in place: the `kept` ones for a kept move, every one for a move the
    set rule holds. Another tree's moves, and a sibling's or a descendant's,
    never reach its judgment. A refusal whose ancestor's move is refused here
    too is left for the next round, judged without it. An update's ranges are
    left to the set rule, which holds rather than refuses; an install's are
    judged here."""
    failing: dict[str, Result] = {}
    above: dict[str, set[str]] = {}
    for addon_id, move in moves.items():
        applied = kept if addon_id in kept else moves
        parents = {p.id: p.parent for p in ctx.loaded if p.repo_id is None}
        parents |= {i: moves[i].candidate.parent for i in applied}
        parents[addon_id] = move.candidate.parent
        # A cycle is `_judge`'s to name; no ancestor's move is taken onto it.
        on = {i for i in inherit.path(addon_id, parents) or () if i in applied} | {addon_id}
        above[addon_id] = on - {addon_id}
        view = [*(p for p in ctx.loaded if p.id not in on), *(moves[i].candidate for i in on)]
        unranged = [replace(p, parent_range=None) for p in view]
        judged = view if move.locked is None else unranged
        rest = [p for p in judged if p.id != addon_id]
        candidate = next(p for p in judged if p.id == addon_id)
        manifests = {i: moves[i].found for i in on}
        try:
            held_by_tree = _judge(move.found, move.extracted.files, candidate, rest, ctx, manifests)
        except Refused as exc:
            failing[addon_id] = Result(addon_id, "refused", problems=tuple(exc.problems))
            continue
        except (fetch.AddonRefused, manifest.ManifestError, OSError) as exc:
            failing[addon_id] = Result(addon_id, "refused", problems=(str(exc),))
            continue
        # A descendant whose ancestry no longer holds is named: its chains
        # would be gone, not broken. One a range leaves out is the set rule's.
        loading = {p.id for p in ctx.loaded if p.left_out is None and p.id not in applied}
        tree = {p.id: p.parent for p in unranged if p.repo_id is None}
        held_by_tree += _dropped(
            [
                p
                for p in inherit.cascade(unranged)
                if addon_id in (inherit.path(p.id, tree) or ())[1:]
            ],
            loading,
        )
        if held_by_tree:
            failing[addon_id] = Result(addon_id, "refused", problems=tuple(held_by_tree))
    first = {i: r for i, r in failing.items() if not above[i] & failing.keys()}
    return first or failing


def _settle(moves: dict[str, _Move], ctx: _Context) -> dict[str, Result]:
    """Phase 3: the set rule over `moves`. A dropped move is taken out of
    `moves`: `held` when it has a lock, `refused` for an install. A loading
    add-on's locked side counts; one left out (a disabled child) holds no
    move."""
    locked = {
        a.id: inherit.Side(a.version, a.parent, a.parent_range)
        for a in ctx.loaded
        if a.repo_id is None and a.left_out is None
    }
    sides = {
        i: inherit.Side(m.found.version, m.candidate.parent, m.candidate.parent_range)
        for i, m in moves.items()
    }
    out: dict[str, Result] = {}
    for addon_id, why in inherit.settle(locked, sides).items():
        outcome = "held" if moves.pop(addon_id).locked is not None else "refused"
        out[addon_id] = Result(addon_id, outcome, problems=(why,))
    return out


def _relatives(ids: Sequence[str], addons: Sequence[load.InstalledAddon]) -> list[str]:
    """Every installed ancestor and descendant of `ids` among `addons`,
    following their `parent` links, `ids` themselves left out."""
    parents = {a.id: a.parent for a in addons if a.repo_id is None}
    lines = {i: inherit.path(i, parents) or (i,) for i in parents}
    out = {
        other
        for addon_id in ids
        for other, line in lines.items()
        if addon_id in line or other in lines.get(addon_id, ())
    }
    return sorted(out - set(ids))


def _unasked(chosen: Mapping[str, Result], joined: Collection[str]) -> dict[str, Result]:
    """`chosen` without the refusals of `joined` (relatives' moves): they were
    not asked for."""
    return {i: r for i, r in chosen.items() if i not in joined or r.outcome != "refused"}


def _review(
    move: _Move,
    old_ancestors: Sequence[load.InstalledAddon],
    new_ancestors: Sequence[load.InstalledAddon],
) -> Review:
    """`move`'s own review, its `said` lines first."""
    from kraft.addons import review as review_mod

    old, said = move.old, move.said
    reviewed = None
    if old is not None:
        try:
            reviewed = review_mod.review(
                move.addon_id,
                old,
                move.extracted,
                old_ancestors=old_ancestors,
                new_ancestors=new_ancestors,
            )
        except Exception:  # noqa: BLE001 -- an edited store can break in any way
            old, said = None, _said(move.locked, move.collection, move.found, None)
    if reviewed is None:
        # Nothing installed, or a store that is gone or does not read:
        # everything in the candidate is shown as new.
        reviewed = review_mod.review(
            move.addon_id, None, move.extracted, new_ancestors=new_ancestors
        )
        if move.locked is not None:
            # Still an update, and one nobody can compare: said first, and held.
            reviewed = replace(reviewed, old_version=move.locked.version)
    return replace(reviewed, reach=(*said, *reviewed.reach))


def _below(
    members: Sequence[_Move],
    loaded: Sequence[load.InstalledAddon],
    would_load: Sequence[load.InstalledAddon],
    said: Collection[str],
) -> dict[str, tuple[list[str], list[str]]]:
    """For each member, the `(reach, content)` lines of the installed, loaded
    descendants that do not move, whose nearest moving ancestor it is: how
    their chains, shadowed names and `remove:` entries change under the
    moves (`a-parent-review-shows-each-descendants-changes`). `said`: lines
    the instance's own chains already gave, which a chain line of a
    descendant (`team:ship.nodes[n]: ...`, named by its chain) does not
    repeat."""
    from kraft.addons import review as review_mod

    moving = {m.addon_id for m in members}
    out: dict[str, tuple[list[str], list[str]]] = {m.addon_id: ([], []) for m in members}
    for d in loaded:
        if d.repo_id is not None or d.left_out is not None or d.id in moving:
            continue
        before = _ancestry_in(d.id, loaded)
        nearest = next((a.id for a in before if a.id in moving), None)
        after = _ancestry_in(d.id, would_load)
        if nearest is None or not after:
            continue
        try:
            # As loading reads it (the manifest was checked, the files are not
            # hashed again), so the review sees what would run.
            lines = review_mod.descendant_lines(d.id, fetch.extract_dir(d.root), before, after)
        except (OSError, ValueError, KeyError, fetch.AddonRefused) as exc:
            # Failing open would let an unattended update change what it runs.
            out[nearest][0].append(
                f"{d.id}: its store does not read ({exc}); its changes cannot be shown"
            )
            continue
        for lane, found in zip(out[nearest], lines, strict=True):
            lane += [line for line in found if line.removeprefix(f"{d.id}: ") not in said]
    return out


def update(
    ids: Sequence[str] | None = None,
    *,
    accept: Callable[[Group], bool],
    re_install: bool = False,
    entries: Mapping[str, AddonEntry | bool] | None = None,
    config_dir: Path | None = None,
    addons_root: Path | None = None,
    relatives: bool = True,
) -> list[Result]:
    """Install or update `ids` (every add-on in `addons.yaml` when None), one
    `Result` each, in `ids`' order. With `relatives`, named `ids` also take the
    waiting moves of their installed ancestors and descendants (none for one
    that is refused or failed), whose results follow in tree order: a
    relative with no move, or whose move is refused before the trees are
    judged, says nothing. One whose move breaks a chain
    that the named moves, and the moves they need, do not is `held` with
    `kind="breaks"`. The moves are settled as a set
    (`inherit.settle`): one a range drops is `held` (`refused` for an
    install). `accept` is asked once per tree that has something to take,
    with a `Group` of its members' reviews, and the tree applies or is
    declined whole. `entries` are new or changed `addons.yaml` entries (an
    install, an alias), held in memory and written only for an add-on that
    was accepted. An install's ancestors that are not installed are pulled
    in with it, each with a default entry, and said after `ids` like a
    relative, its refusals too; each applies only with a child that builds
    on it. `re_install` takes the commit the ref resolves to now even at an
    unchanged version, and takes no relative (not a descendant's waiting
    move, which a plain update takes) but the waiting move of an ancestor
    whose locked version the re-installed range rejects and whose move it
    accepts, and that only with the child. Raises
    `Refused` when `entries` do not fit `addons.yaml`; `ConfigError` or
    `PolicyError` when `addons.yaml`, the lock or `policy.yaml` cannot be
    read."""
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

    def configured() -> AddonsConfig:
        try:
            return AddonsConfig.model_validate(
                {**written, "addons": {**(written.get("addons") or {}), **new_entries}}
            )
        except ValidationError as exc:
            raise Refused([first_error(exc, AddonsConfig.FILE)]) from exc

    config = configured()
    order = list(dict.fromkeys(ids if ids is not None else config.addons))
    results: dict[str, Result] = {}
    accepted: list[_Move] = []
    with tempfile.TemporaryDirectory() as scratch:
        loaded = list(load.installed(config_dir, addons_root))
        state = state_of(config_dir, loaded)
        ctx = _Context(
            config=config,
            lock=lock,
            loaded=tuple(loaded),
            running=kraft_update.installed(),
            catalogues={},
            config_dir=config_dir,
            addons_root=addons_root,
            scratch=Path(scratch),
            re_install=re_install,
        )
        # 1. Resolve.
        moves: dict[str, _Move] = {}
        for addon_id in order:
            resolved = _resolve(addon_id, ctx)
            if isinstance(resolved, _Move):
                moves[addon_id] = resolved
            else:
                results[addon_id] = resolved
        # Pull-in: an install's ancestors that are neither installed nor
        # moving are installed with it, each under its own name with a default
        # entry, up the tree. Each is said, and each refusal refuses the
        # installs below it (`_orphans`).
        pulled: dict[str, list[str]] = {}
        climbing = [i for i in order if i in moves]
        while climbing:
            child = moves[climbing.pop(0)]
            parent_id = child.candidate.parent
            if child.locked is not None or parent_id is None:
                continue
            if parent_id in pulled:
                pulled[parent_id].append(child.addon_id)
                continue
            if parent_id in lock.addons or parent_id in moves or parent_id in results:
                continue
            # One listed but turned off is the operator's to turn on, not ours.
            if parent_id in config.addons and not config.entry(parent_id).enabled:
                del moves[child.addon_id]
                results[child.addon_id] = Result(
                    child.addon_id,
                    "refused",
                    problems=(
                        f"its parent {parent_id} is not loaded: {load.DISABLED}; "
                        f"enable it first: kraft admin addon enable {parent_id}",
                    ),
                )
                continue
            if parent_id.split("@", 1)[1] not in config.collections:
                del moves[child.addon_id]
                results[child.addon_id] = Result(
                    child.addon_id, "refused", problems=(_missing_collection(parent_id),)
                )
                continue
            pulled[parent_id] = [child.addon_id]
            name = parent_id.split("@", 1)[0]
            holders = {config.namespace(o): o for o in config.addons} | {
                e.namespace: o for o, e in lock.addons.items()
            }
            holder = holders.get(
                config.namespace(parent_id) if parent_id in config.addons else name
            )
            if holder not in (None, parent_id):
                results[parent_id] = Result(
                    parent_id,
                    "refused",
                    problems=(
                        f"{parent_id} installs under its name {name}, which {holder} holds; "
                        "install it first with its own --as: kraft admin addon install "
                        f"{parent_id} --as ALIAS",
                    ),
                )
                continue
            if parent_id not in config.addons:
                new_entries[parent_id] = True
                config = configured()
                ctx = replace(ctx, config=config)
            resolved = _resolve(parent_id, ctx)
            if isinstance(resolved, _Move):
                moves[parent_id] = resolved
                climbing.append(parent_id)
            else:
                results[parent_id] = resolved
        # Re-install: an installed ancestor stays at its locked version unless
        # the re-installed range rejects it; then its waiting move is taken
        # when the range accepts it, up the tree, and only with the child
        # (`_orphans`). A re-install takes no other relative.
        needs: dict[str, list[str]] = {}
        climbing = [i for i in order if i in moves] if re_install else []
        while climbing:
            child = moves[climbing.pop(0)]
            parent_id = child.candidate.parent
            parent = next((p for p in loaded if p.id == parent_id and p.repo_id is None), None)
            if parent is None or not _outside(child.candidate.parent_range, parent.version):
                continue
            if parent_id in needs:
                needs[parent_id].append(child.addon_id)
                continue
            if parent_id in moves or parent_id in results:
                continue
            resolved = _resolve(parent_id, ctx)
            if isinstance(resolved, _Move):
                # One the range rejects too: the set rule holds the child.
                if _outside(child.candidate.parent_range, resolved.found.version):
                    continue
                moves[parent_id] = resolved
                climbing.append(parent_id)
            else:
                results[parent_id] = resolved
            needs[parent_id] = [child.addon_id]
        # Scope: a named add-on's installed relatives join with their waiting
        # moves. Anything else said of a relative is not said: it was not asked.
        # A named add-on that is refused or failed takes none, which would
        # move without it; one that is current takes them.
        joined: dict[str, None] = {}
        taking = [i for i in order if i in moves or results[i].outcome not in ("refused", "failed")]
        scope = (
            _relatives(taking, loaded) if ids is not None and relatives and not re_install else []
        )
        for addon_id in [i for i in scope if i not in order]:
            resolved = _resolve(addon_id, ctx)
            if isinstance(resolved, _Move):
                moves[addon_id] = resolved
                joined[addon_id] = None
        # Said after `ids`, in tree order, with the parents their moves name.
        # Taken before `_choose` drops a refused move: one refused
        # there (a pulled-in ancestor, say) is still said in its tree.
        # `parents` below is taken after it.
        said_parents = {p.id: p.parent for p in loaded if p.repo_id is None}
        said_parents |= {i: m.candidate.parent for i, m in moves.items()}
        # 2. Check, and 3. settle, to a fixed point.
        gone = {i: r for i, r in results.items() if i not in lock.addons}
        builders = {**pulled, **needs}
        results |= _unasked(_choose(moves, ctx, builders, gone), joined)
        # 4. Trees: one review and one answer each.
        parents = {p.id: p.parent for p in loaded if p.repo_id is None}
        parents |= {i: m.candidate.parent for i, m in moves.items()}
        sides = {
            a.id: inherit.Side(a.version, a.parent, a.parent_range)
            for a in loaded
            if a.repo_id is None and a.left_out is None
        }

        def judged(members: Sequence[_Move]) -> _Judged:
            """The instance with `members` applied, and what they break in it,
            each scratch root said as its add-on's id."""
            tree = {m.addon_id for m in members}
            would_load = list(
                inherit.cascade(
                    [*(p for p in loaded if p.id not in tree), *(m.candidate for m in members)]
                )
            )
            after = state_of(config_dir, would_load)
            libraries = {p.namespace for p in would_load if p.repo_id is not None}
            said = breaks(
                state, after, config_dir, [m.namespace for m in members], exempt=libraries
            )
            for m in members:
                said = [why.replace(str(m.candidate.root), m.addon_id) for why in said]
            return _Judged(would_load, after, libraries, said)

        for tree in inherit.trees(moves, parents):
            members = [moves[i] for i in tree]
            would_load, after, libraries, broken = judged(members)
            if broken and joined.keys() & set(tree):
                # Judged again without the relatives: only the named add-ons'
                # moves and the moves they need. When that holds, each other relative waits.
                need = inherit.needed(
                    [i for i in tree if i not in joined],
                    sides,
                    {
                        m.addon_id: inherit.Side(
                            m.found.version, m.candidate.parent, m.candidate.parent_range
                        )
                        for m in members
                    },
                )
                again = judged([m for m in members if m.addon_id in need])
                if not again.broken:
                    for m in members:
                        if m.addon_id not in need:
                            results[m.addon_id] = Result(
                                m.addon_id, "held", problems=tuple(broken), kind="breaks"
                            )
                    # The needed moves were checked with the held ones in place,
                    # but apply over their locked sides: checked again so.
                    needed = {m.addon_id: m for m in members if m.addon_id in need}
                    results |= _unasked(_choose(needed, ctx, builders), joined)
                    members = [m for m in members if m.addon_id in needed]
                    if not members:
                        continue
                    if len(needed) < len(need):
                        again = judged(members)
                    would_load, after, libraries, broken = again
                else:
                    broken += [why for why in again.broken if why not in broken]
            reviews = [
                _review(
                    m,
                    old_ancestors=_ancestry_in(m.addon_id, loaded),
                    new_ancestors=_ancestry_in(m.addon_id, would_load),
                )
                for m in members
            ]
            namespaces = [m.namespace for m in members]
            # A break the named moves cannot shed refuses every member.
            if broken:
                for m, reviewed in zip(members, reviews, strict=True):
                    results[m.addon_id] = Result(
                        m.addon_id, "refused", review=reviewed, problems=tuple(broken)
                    )
                continue
            held = held_breaks(state, after, libraries)
            # A descendant's chains read a member's components late, so an
            # update here can change them: they are diffed like the instance's.
            # A member's own chains are in its own review.
            descendants = {
                ns
                for m in members
                for ns in _descendants(m.addon_id, loaded) | _descendants(m.addon_id, would_load)
            } - set(namespaces)
            reach, content = (
                review_mod.local_changes(
                    state.library,
                    after.library,
                    namespaces,
                    extra=_chains_of(libraries | descendants, after),
                )
                if after.library is not None
                else ([], [])
            )
            below = _below(members, loaded, would_load, {*reach, *content})
            for index, (m, reviewed) in enumerate(zip(members, reviews, strict=True)):
                captured = (
                    [
                        f"{where}: {key} {value!r} is now read from this add-on"
                        for where, key, _kind, value in references(config_dir)
                        if _qualifier(value) == m.namespace
                    ]
                    if m.locked is None or m.locked.namespace != m.namespace
                    else []
                )
                # The tree's own lines go to its first member.
                first = index == 0
                reviews[index] = replace(
                    reviewed,
                    reach=(
                        *(held if first else ()),
                        *reviewed.reach,
                        *captured,
                        *(reach if first else ()),
                        *below[m.addon_id][0],
                    ),
                    content=(
                        *reviewed.content,
                        *(content if first else ()),
                        *below[m.addon_id][1],
                    ),
                )
            # A pulled-in ancestor names the members it is taken for.
            here = {m.addon_id for m in members}
            on = {i: [c for c in pulled[i] if c in here] for i in here & pulled.keys()}
            pulled_in = tuple(
                f"pulls in {m.addon_id} {m.found.version} from collection "
                f"{m.addon_id.split('@', 1)[1]}: {', '.join(on[m.addon_id])} "
                f"{'builds' if len(on[m.addon_id]) == 1 else 'build'} on it"
                for m in members
                if m.addon_id in pulled
            )
            if not accept(review_mod.Group(tuple(reviews), pulled_in)):
                for m, reviewed in zip(members, reviews, strict=True):
                    results[m.addon_id] = Result(m.addon_id, "declined", review=reviewed)
                continue
            loaded, state = would_load, after
            for m, reviewed in zip(members, reviews, strict=True):
                results[m.addon_id] = Result(m.addon_id, "applied", review=reviewed)
                accepted.append(m)

    if accepted:
        try:
            with write_lock(addons_root):
                if [f.read_bytes() if f.exists() else None for f in files] != seen:
                    raise Busy("add-ons changed while you reviewed; run update again")
                for done in accepted:
                    fetch.write_store(addons_root, done.extracted)
                    if done.mirror is not None:
                        fetch.pin(done.mirror, done.locks_as().commit)
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
                locks = AddonsLock(addons=kept | {d.addon_id: d.locks_as() for d in accepted})
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
    taken = [i for tree in inherit.trees([*joined, *pulled, *needs], said_parents) for i in tree]
    return [results[i] for i in [*order, *taken] if i in results]


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


def unattended_holds(group: Group) -> dict[str, str]:
    """Why an auto-update holds this group, member by member; empty when it
    may apply on its own. A group goes whole or not at all: a member with no
    reason of its own is held `with` the first member that has one."""
    own = {r.addon_id: may_apply_unattended(r) for r in group.reviews}
    first = next(((i, why) for i, why in own.items() if why is not None), None)
    if first is None:
        return {}
    return {i: why if why is not None else f"with {first[0]}: {first[1]}" for i, why in own.items()}


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

    def accept(group: Group) -> bool:
        whys = unattended_holds(group)
        held.update(whys)
        return not whys

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
            collection_ids,
            accept=accept,
            config_dir=config_dir,
            addons_root=addons_root,
            relatives=False,
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
            "held": {
                "outcome": "held",
                "message": f"{said}; run kraft admin addon update to move it with its "
                "relatives, or wait for a release that fits",
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
