"""An installed Kraft add-on as the template library and the agent-profile
table read it.

A leaf module on purpose: it imports nothing from `kraft.templates` or
`kraft.config`. `kraft.config` imports `kraft.templates.environment`, which
reads add-ons, so an import the other way would be a cycle.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kraft.addons.manifest import AddonManifest

#: A repo library's id is `library@repo:<repository id>`. `:` is in no
#: collection name, so `addons.yaml` can never hold one.
_REPO = "repo:"


def repo_library_id(repo_id: str) -> str:
    return f"library@{_REPO}{repo_id}"


#: The `left_out` of an add-on `addons.yaml` disables.
DISABLED = "is disabled"


@dataclass(frozen=True)
class InstalledAddon:
    """One add-on the instance loads: where its extracted files are and the
    namespace everything it declares is addressed under."""

    #: `<addon>@<collection>`.
    id: str
    #: The name its `addon.json` declares.
    name: str
    #: `name`, or the alias it was installed under.
    namespace: str
    version: str
    #: Its extracted files, in the fixed layout: `library.yaml`, `chains/`,
    #: `skills/`, `profiles.yaml`.
    root: Path
    #: Why this add-on is known to the instance but not loaded; None when it loads.
    left_out: str | None = None
    #: An add-on left out on purpose (disabled, not yet installed, orphaned in
    #: the lock) is no fault: health does not report it.
    quiet: bool = False
    #: Where the lock says its files came from: the collection's commit, the
    #: add-on's directory in it and that directory's tree. None for a
    #: directory collection, and for an add-on that is not locked.
    commit: str | None = None
    tree: str | None = None
    source: str | None = None
    #: The id of the add-on this one builds on (`requires.addons`), and the
    #: range of its versions accepted; None for a root add-on, and until its
    #: manifest has been read.
    parent: str | None = None
    parent_range: str | None = None

    @property
    def pin(self) -> dict[str, str | None]:
        """What a work item records of this add-on: enough to find its store,
        and to restore it from its commit when the store is gone. A child's
        also names its parent, so the pinned tree is rebuilt as it was even
        when a store is gone; a root's has no `parent` key."""
        pin: dict[str, str | None] = {
            "id": self.id,
            "commit": self.commit,
            "tree": self.tree,
            "source": self.source,
            "digest": f"sha256:{self.root.name}",
            "version": self.version,
        }
        if self.parent is not None:
            pin["parent"] = self.parent
        return pin

    @property
    def repo_id(self) -> str | None:
        """The repository this is the repo library of; None for an add-on."""
        collection = self.id.partition("@")[2]
        return collection.removeprefix(_REPO) if collection.startswith(_REPO) else None


def scope_message(name: str, repo_id: str) -> str:
    """Why `name` cannot be read here: it is in `repo_id`'s repo library. The
    one wording every door that refuses a repo library from outside uses."""
    return f"{name} is in {repo_id}'s repo library, {_visible_only(repo_id)}"


def _visible_only(repo_id: str) -> str:
    return f"visible only to items in {repo_id}"


def _library_out_of_scope(repo_id: str) -> str:
    return f"{repo_id}'s repo library is {_visible_only(repo_id)}"


def out_of_scope(name: object, owner: str | None, repo_ids: Collection[str]) -> str | None:
    """`name` (`<q>:<x>`) names a connected repository's library (`q` in
    `repo_ids`) from outside it (`q` is not `owner`): the reason, else None."""
    if not isinstance(name, str):
        return None
    qualifier, colon, _ = name.partition(":")
    if not colon or qualifier not in repo_ids or qualifier == owner:
        return None
    return scope_message(name, qualifier)


def _pin_repo(pin: Mapping[str, str | None]) -> str | None:
    """The repository a pin's `library@repo:<id>` names; None for an add-on."""
    collection = str(pin.get("id") or "").partition("@")[2]
    return collection.removeprefix(_REPO) if collection.startswith(_REPO) else None


def scope_problem(
    pins: Mapping[str, Mapping[str, str | None]] | None, repo_id: str | None
) -> str | None:
    """Why a chain pinned as `pins` cannot run for `repo_id`'s items: the
    first repo library it reads that is not `repo_id`'s. None when every pin
    is in scope. `repo_id` None (no repository, or an entry with no `id`)
    refuses every repo library; an add-on is never scoped."""
    for pin in (pins or {}).values():
        owner = _pin_repo(pin)
        if owner is not None and owner != repo_id:
            return f"it reads {owner}'s repo library, {_visible_only(owner)}"
    return None


def scoped(
    addons: Iterable[InstalledAddon], repo_id: str | None, keep: Collection[str] = ()
) -> tuple[InstalledAddon, ...]:
    """`addons` as `repo_id`'s items see them: each repo library that is not
    `repo_id`'s, and whose namespace is not in `keep` (pinned by the item
    already), left out, quietly. Never dropped: a namespace missing from a
    launch's add-ons is read as another tool's plugin."""
    return tuple(
        replace(a, left_out=_library_out_of_scope(a.namespace), quiet=True)
        if a.repo_id is not None and a.repo_id != repo_id and a.namespace not in keep
        else a
        for a in addons
    )


def _reference(value: str, addon: InstalledAddon, *, bare_is_own: bool) -> str:
    qualifier, colon, name = value.partition(":")
    if not colon:
        return f"{addon.namespace}:{value}" if bare_is_own else value
    return f"{addon.namespace}:{name}" if qualifier == addon.name else value


def qualify(data: object, addon: InstalledAddon) -> object:
    """`data`, an add-on's parsed YAML, with its references made absolute.

    A bare `extends:`, `steering:` or `skill:` names the add-on's own
    declaration, and so does one qualified by the add-on's own name; both
    become `<namespace>:<name>`, so an aliased add-on works unchanged. A bare
    `profile:` is the instance's agent profile and stays bare; only the
    add-on's own name in it is rewritten. Any other qualifier (`kraft:`,
    another tool's plugin) and every `harness:` is left as written.
    """
    if isinstance(data, list):
        return [qualify(item, addon) for item in data]
    if not isinstance(data, Mapping):
        return data
    out: dict[object, object] = {}
    for key, value in data.items():
        if key in ("extends", "skill") and isinstance(value, str):
            out[key] = _reference(value, addon, bare_is_own=True)
        elif key == "steering" and isinstance(value, list):
            out[key] = [
                _reference(v, addon, bare_is_own=True) if isinstance(v, str) else v for v in value
            ]
        elif key == "profile" and isinstance(value, str):
            out[key] = _reference(value, addon, bare_is_own=False)
        else:
            out[key] = qualify(value, addon)
    return out


def builds_on(library: object) -> tuple[str, ...]:
    """The add-on ids a repo library's `library.yaml` lists under `addons:`,
    as written. An entry that is not a string is left out here;
    `update.problems` says why it is refused."""
    listed = library.get("addons") if isinstance(library, Mapping) else None
    return tuple(v for v in listed if isinstance(v, str)) if isinstance(listed, list) else ()


def alias_extends(written: object, qualified: object, aliases: Mapping[str, str]) -> object:
    """`qualified` (`qualify(written, addon)`), a repo library's chain file,
    with the top-level `extends` that `written` names a listed add-on by
    (`base:default`) set to the namespace this instance loads that add-on
    under (`o:default`). The same committed file then means the same add-on
    on every instance. The written value is read, not the qualified one:
    `qualify` has made a bare `default` into `<repo id>:default`, and a repo
    id may be a listed add-on's name on an instance that aliased it."""
    if not isinstance(written, Mapping) or not isinstance(qualified, Mapping):
        return qualified
    parent = written.get("extends")
    if not isinstance(parent, str):
        return qualified
    qualifier, colon, name = parent.partition(":")
    if not colon or qualifier not in aliases:
        return qualified
    return {**qualified, "extends": f"{aliases[qualifier]}:{name}"}


def addons_dir() -> Path:
    """This process's `run/addons`: `KRAFT_RUN_DIR`, else the default run directory."""
    from kraft.paths import RunDirs, default_run_dir

    return RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).addons


def config_problem(config_dir: Path) -> str | None:
    """Why `addons.yaml`, `addons.lock` or `repo-libraries.lock` in
    `config_dir` cannot be read, or None. While one cannot, no add-on loads."""
    from kraft.addons.config import AddonsConfig, AddonsLock, RepoLibrariesLock
    from kraft.config import ConfigError

    try:
        AddonsConfig.load(Path(config_dir) / AddonsConfig.FILE)
        AddonsLock.load(Path(config_dir) / AddonsLock.FILE)
        RepoLibrariesLock.load(Path(config_dir) / RepoLibrariesLock.FILE)
    except ConfigError as exc:
        return str(exc)
    return None


#: `installed()`'s last answer per (config dir, store root) and what it was
#: computed from. Replaced whole, never mutated: a thread and the loop cannot
#: corrupt it; the worst race is one redundant recompute.
_memo: dict[tuple[str, str], tuple[tuple, tuple[InstalledAddon, ...]]] = {}


def stamp(path: Path) -> tuple | None:
    """`path`'s (mtime_ns, size, inode), or None when it cannot be statted."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def signature(config_dir: Path, found: tuple[InstalledAddon, ...] = ()) -> tuple:
    """Everything `installed()` reads, as stat triples (None: absent), so an
    unchanged answer can be told without re-parsing: the six config files,
    the harness overlay directory, `found`'s stores (existence, manifest,
    digest file), the restore failures `_store_problem` quotes, and this
    Kraft's version, which decides `requires.kraft`."""
    from kraft import paths, update
    from kraft.addons import fetch, manifest

    config_dir = Path(config_dir)
    names = (
        "addons.yaml",
        "addons.lock",
        "repo-libraries.lock",
        "harnesses.yaml",
        "repos.yaml",
        "policy.yaml",
    )
    store_files = (manifest.ADDON_JSON, fetch.DIGEST_FILE)
    try:
        overlaid = sorted(paths.default_harnesses_dir().glob("*.yaml"))
    except OSError:
        overlaid = []
    return (
        tuple(stamp(config_dir / n) for n in names),
        tuple((p.name, stamp(p)) for p in overlaid),
        tuple((p.root.is_dir(), *(stamp(p.root / f) for f in store_files)) for p in found),
        tuple(sorted(_unrestored.items())),
        update.installed(),
    )


def installed(
    config_dir: Path | None = None,
    addons_root: Path | None = None,
    *,
    verify: bool = False,
    config=None,
) -> tuple[InstalledAddon, ...]:
    """Every add-on `addons.yaml` or `addons.lock` names, each either
    loadable (`left_out` None) or carrying why it is not.

    An add-on loads when it has an enabled `addons.yaml` entry and a lock
    entry, from the store the lock names and under the namespace the lock
    recorded: an alias changed by hand waits for an update. Nothing is fetched
    here, ever. `verify` re-hashes every stored file, not only the manifest.
    `config` is an `AddonsConfig` to read in place of the file: an edit of
    `addons.yaml` not saved yet.

    The answer is kept until a file it was computed from changes (see
    `signature`); a `verify` or `config` call is never answered from it.
    """
    global _memo
    from kraft import paths

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    root = Path(addons_root) if addons_root is not None else addons_dir()
    if verify or config is not None:
        return _installed(config_dir, root / "store", verify, config)
    key = (str(config_dir), str(root))
    hit = _memo.get(key)
    if hit is not None and hit[0] == signature(config_dir, hit[1]):
        return hit[1]
    before = signature(config_dir)
    found = _installed(config_dir, root / "store", verify, config)
    after = signature(config_dir, found)
    # An input that changed during the compute is not what `found` was made
    # from (the stores, index 2, are derived from `found` and so not compared).
    if (before[:2], before[3:]) == (after[:2], after[3:]):
        _memo = {**_memo, key: (after, found)}
    return found


def _installed(config_dir: Path, store: Path, verify: bool, config) -> tuple[InstalledAddon, ...]:
    """In four passes: each add-on's own store (and the parent its manifest
    names); the left-out cascade; each loading add-on's content, judged with
    its ancestors, parents before children; the cascade again, which leaves
    out the children of a parent the third pass left out."""
    from kraft.addons import inherit
    from kraft.addons.config import AddonsConfig, AddonsLock
    from kraft.config import ConfigError

    try:
        config = config or AddonsConfig.load(config_dir / AddonsConfig.FILE)
        lock = AddonsLock.load(config_dir / AddonsLock.FILE)
    except ConfigError:
        return ()  # `config_problem` says why
    found: list[InstalledAddon] = []
    #: Every manifest pass 1 read, by add-on id: pass 3 never reads a store again.
    manifests: dict[str, AddonManifest] = {}
    for addon_id in config.addons:
        name = addon_id.split("@", 1)[0]
        locked = lock.addons.get(addon_id)
        if locked is None:
            found.append(
                InstalledAddon(
                    addon_id,
                    name,
                    config.namespace(addon_id),
                    "",
                    store / "not-installed",
                    left_out="is not installed; run `kraft admin addon install`",
                    quiet=True,
                )
            )
            continue
        addon = InstalledAddon(
            addon_id,
            name,
            locked.namespace,
            locked.version,
            store / locked.digest.removeprefix("sha256:"),
            commit=locked.commit,
            tree=locked.tree,
            source=locked.source,
        )
        if not config.entry(addon_id).enabled:
            found.append(replace(addon, left_out=DISABLED, quiet=True))
        else:
            found.append(_with_store_problem(addon, verify, manifests))
    for addon_id, locked in lock.addons.items():
        if addon_id not in config.addons:
            found.append(
                InstalledAddon(
                    addon_id,
                    addon_id.split("@", 1)[0],
                    locked.namespace,
                    locked.version,
                    store / locked.digest.removeprefix("sha256:"),
                    left_out="is locked but not listed in addons.yaml",
                    quiet=True,
                )
            )
    found += _repo_libraries(config_dir, store, verify, manifests)
    judged = list(inherit.cascade(found))
    depth = [len(inherit.ancestry(a, judged)) if a.left_out is None else 0 for a in judged]
    for i in sorted(range(len(judged)), key=depth.__getitem__):
        addon = judged[i]
        if addon.left_out is not None:
            continue
        try:
            chain = inherit.ancestry(addon, judged)
        except inherit.InheritanceError:
            continue  # an ancestor was just left out: the cascade says so
        why = _content_problem(addon, chain, config_dir, manifests)
        judged[i] = replace(addon, left_out=why)
    return inherit.cascade(judged)


def _repo_libraries(
    config_dir: Path, store: Path, verify: bool, manifests: dict[str, AddonManifest]
) -> list[InstalledAddon]:
    """Each repository's accepted repo library, as the add-on it is stored as.
    One whose repository is no longer in `repos.yaml` under that id is known
    and not loaded, and is no fault: the next accept or disconnect drops it."""
    from kraft.addons.config import RepoLibrariesLock
    from kraft.config import ConfigError, read_yaml

    try:
        lock = RepoLibrariesLock.load(config_dir / RepoLibrariesLock.FILE)
        entries = read_yaml(config_dir / "repos.yaml", {}).get("repos")
    except ConfigError:
        return []  # `config_problem`, or repos.yaml's own check, says why
    connected = {e.get("id") for e in entries or () if isinstance(e, dict)}
    out = []
    for repo_id, locked in lock.repos.items():
        digest = locked.digest.removeprefix("sha256:")
        addon = InstalledAddon(
            repo_library_id(repo_id),
            "library",
            repo_id,
            digest[:12],
            store / digest,
            commit=locked.commit,
            tree=locked.tree,
            source="./.kraft",
        )
        if repo_id not in connected:
            out.append(replace(addon, left_out="its repository is no longer connected", quiet=True))
        else:
            out.append(_with_store_problem(addon, verify, manifests))
    return out


def _with_store_problem(
    addon: InstalledAddon, verify: bool, manifests: dict[str, AddonManifest]
) -> InstalledAddon:
    """`addon` with its store's problem as `left_out`, and the parent its
    manifest names filled in. A manifest read is kept in `manifests`."""
    declared, why = _store_problem(addon, verify)
    if declared is not None:
        manifests[addon.id] = declared
    parent = declared.parent if declared is not None else None
    if parent is None:
        return replace(addon, left_out=why)
    return replace(addon, left_out=why, parent=parent[0], parent_range=parent[1])


def _store_problem(addon: InstalledAddon, verify: bool) -> tuple[AddonManifest | None, str | None]:
    """`addon`'s manifest, and the first check of its own store it fails: the
    store is there, its files match the locked digest, its `addon.json`
    parses, and this Kraft is one it accepts. The manifest is None when it
    could not be read; the problem is None when all pass."""
    from kraft import update
    from kraft.addons import fetch, inherit, manifest

    root = addon.root
    if not root.is_dir():
        if why := _unrestored.get(root.name):
            return None, f"its store could not be restored: {why}"
        return None, "its store is missing and has not been restored"
    try:
        # Bounded and no-follow: the manifest must hash to the store's name.
        if verify:
            fetch.verify_store(root)
        declared = inherit.read_manifest(addon)
    except fetch.StoreMismatch:
        return None, "its files do not match the locked digest"
    except (OSError, ValueError, fetch.AddonRefused, manifest.ManifestError) as exc:
        return None, f"its store cannot be read: {exc}"
    running = update.installed()
    if addon.repo_id is None and not manifest.is_source_build(running):
        if why := manifest.kraft_compatible(declared.requires.kraft, running):
            return declared, why
    return declared, None


def _content_problem(
    addon: InstalledAddon,
    chain: tuple[InstalledAddon, ...],
    config_dir: Path,
    manifests: Mapping[str, AddonManifest],
) -> str | None:
    """The first check `addon`'s content fails on this instance, judged with
    its ancestors (`chain`, `inherit.ancestry`'s answer): what the tree
    requires of `harnesses.yaml` and `repos.yaml`, its library and agent
    profiles read with theirs, and its limits under `maxima:`. None when it
    loads. `manifests` is what the store pass read, by add-on id."""
    from kraft import harness
    from kraft.addons import inherit
    from kraft.config import ConfigError, read_yaml
    from kraft.policy import PolicyError, PolicyInput
    from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    if why := instance_problem(
        addon.namespace,
        inherit.requirements(chain, manifests),
        config_dir,
        repo_library=addon.repo_id is not None,
    ):
        return why
    try:
        harnesses = read_yaml(config_dir / "harnesses.yaml")
    except ConfigError:
        return None  # that file's own check says why
    # Its own files and its ancestors', read without the rest: an add-on that
    # does not load must not take the library or the profile table down with it.
    try:
        TemplateLibrary.from_mappings(
            {}, (), library_path=config_dir / "library.yaml", addons=list(chain)
        )
        HarnessProfileTable.from_mapping(
            harnesses,
            config_dir / "harnesses.yaml",
            harnesses=harness.load(None).valid,
            addons=list(chain),
        )
    except (TemplateLibraryError, TemplateEnvironmentError) as exc:
        if any(str(a.root) in str(exc) or f"profiles.{a.namespace}:" in str(exc) for a in chain):
            return str(exc)
        return None
    try:
        policy = PolicyInput.from_yaml(config_dir / "policy.yaml").instance_policy()
    except PolicyError:
        return None  # no policy.yaml, or one whose own check says why
    return limits_problem(addon, policy, chain[1:])


def limits_problem(
    addon: InstalledAddon, instance_policy, ancestors: Sequence[InstalledAddon] = ()
) -> str | None:
    """Why one of `addon`'s limits no longer fits under `instance_policy`'s
    `maxima:`, lowered since the add-on was installed; None when all fit.
    `ancestors` (parent first) are read with it, so what it inherits and
    extends is checked too."""
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    try:
        library = TemplateLibrary.from_mappings(
            {}, (), library_path=addon.root / "local-library.yaml", addons=[addon, *ancestors]
        )
    except TemplateLibraryError:
        return None
    unbounded = {issue.message for issue in library.lint()}
    over = sorted({issue.message for issue in library.lint(instance_policy)} - unbounded)
    return over[0] if over else None


def instance_problem(
    namespace: str,
    requires,
    config_dir: Path,
    harnesses: Mapping | None = None,
    repos: Mapping | None = None,
    repo_library: bool = False,
) -> str | None:
    """Why this instance cannot hold an add-on under `namespace` that declares
    `requires` (its manifest's): a harness or agent profile `harnesses.yaml`
    does not define, or a repository whose id is the namespace. None when it
    can. Asked at load and of every install or update candidate. `harnesses`
    is a `harnesses.yaml` to judge in place of the file, `repos` likewise a
    `repos.yaml`: an edit not saved yet. `repo_library`: the namespace is a
    repository's own id, which must be there."""
    from kraft.config import ConfigError, read_yaml

    try:
        if harnesses is None:
            harnesses = read_yaml(config_dir / "harnesses.yaml")
        if repos is None:
            repos = read_yaml(config_dir / "repos.yaml")
    except ConfigError:
        return None  # that file's own check says why
    for section, what, wanted in (
        ("harnesses", "harness", requires.harnesses),
        ("profiles", "agent profile", requires.profiles),
    ):
        defined = harnesses.get(section)
        for name in wanted:
            if not isinstance(defined, dict) or name not in defined:
                return f"requires {what} {name!r}, which harnesses.yaml does not define"
    entries = repos.get("repos")
    entries = entries if isinstance(entries, list) else ()
    ids = {e.get("id") for e in entries if isinstance(e, dict)}
    if repo_library:
        if namespace not in ids:
            return f"repository id {namespace!r} is no longer in repos.yaml"
    elif namespace in ids:
        return f"its namespace {namespace!r} is now a repository id"
    return None


def for_item(
    pins: Mapping[str, Mapping[str, str | None]] | None,
    config_dir: Path | None = None,
    addons_root: Path | None = None,
    *,
    repo_id: str | None,
) -> tuple[InstalledAddon, ...]:
    """The add-ons one work item reads: the versions its chain was
    materialized with (`pins`, namespace to `InstalledAddon.pin`), and the
    lock's for every namespace it did not pin. A pinned version whose store
    is gone is known and not loaded: its skills are refused, never handed to
    the agent as another tool's.

    `repo_id` is the item's repository (a workspace's root): every other
    repo library it did not pin is left out (`scoped`). None, no repository,
    leaves every unpinned repo library out.

    Every connected repository id that names no loaded or pinned add-on
    comes back too, left out: `<repo-id>:<skill>` is then refused, never
    handed to the agent as another tool's plugin, whether or not that
    repository's library is accepted.

    A pinned child builds on the parent its pin names (its store's manifest,
    for a pin stored before pins named parents), and the add-ons come back
    parents first. One whose ancestor is not loaded is left out with that
    ancestor's reason."""
    from kraft.addons import inherit  # inherit imports this module

    store = (Path(addons_root) if addons_root is not None else addons_dir()) / "store"
    pinned = []
    for namespace, pin in (pins or {}).items():
        addon_id, digest = str(pin["id"]), str(pin["digest"])
        root = store / digest.removeprefix("sha256:")
        pinned.append(
            InstalledAddon(
                addon_id,
                addon_id.split("@", 1)[0],
                namespace,
                str(pin["version"]),
                root,
                left_out=None
                if root.is_dir()
                else _unrestored.get(root.name)
                or "the version this work item started with is no longer in the store",
                commit=pin.get("commit"),
                tree=pin.get("tree"),
                source=pin.get("source"),
                parent=pin.get("parent") if "parent" in pin else _pinned_parent(root),
            )
        )
    live = scoped(
        (p for p in installed(config_dir, addons_root) if p.namespace not in (pins or {})),
        repo_id,
    )
    # After `scoped`: a store out of this item's scope is never hashed.
    checked = (
        replace(addon, left_out=why)
        if addon.left_out is None and (why := _launch_problem(addon))
        else addon
        for addon in (*live, *pinned)
    )
    # A child of a pinned ancestor that is gone is left out with its reason,
    # never built on the lock's version of that ancestor.
    loading = inherit.tree_order(inherit.cascade(tuple(checked)))
    taken = {p.namespace for p in loading}
    reserved = [
        InstalledAddon(
            repo_library_id(rid),
            "library",
            rid,
            "",
            store / "not-accepted",
            left_out=f"repository {rid!r} has no accepted repo library",
            quiet=True,
        )
        for rid in connected_ids(config_dir, addons_root=addons_root)
        if rid not in taken
    ]
    return (*loading, *reserved)


def _pinned_parent(root: Path) -> str | None:
    """The parent a pinned add-on's own manifest names, for a pin stored
    before pins named parents, so a pinned child still builds as a child;
    None for a root add-on, or a store that does not read (the launch says
    why). Such a snapshot did not pin the ancestors either: they are the
    instance's installed ones. Its range is not checked again here."""
    from kraft.addons import fetch, inherit, manifest

    if not root.is_dir():
        return None
    try:
        declared = inherit.read_manifest(InstalledAddon("", "", "", "", root))
    except (OSError, ValueError, fetch.StoreMismatch, fetch.AddonRefused, manifest.ManifestError):
        return None  # the launch reads the store again and says why
    return declared.parent[0] if declared.parent is not None else None


def connected_ids(
    config_dir: Path | None, repos: Mapping | None = None, *, addons_root: Path | None = None
) -> list[str]:
    """Every connected repository's id in `config_dir`'s `repos.yaml` (or in
    `repos`, one not saved yet) that is a repo library's namespace: each is
    reserved for that repository's library. Not `kraft`, and not a namespace
    an add-on holds: such an id (set by hand, or minted before ids skipped
    them) never names a repo library. None when the file cannot be read (its
    own check says why)."""
    from kraft import paths
    from kraft.addons.config import RESERVED_NAMESPACE
    from kraft.addons.repo_library import held_namespaces
    from kraft.config import ConfigError, read_yaml

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    try:
        repos = repos if repos is not None else read_yaml(config_dir / "repos.yaml", {})
        entries = repos.get("repos")
    except ConfigError:
        return []
    # A draft's `repos:` may be anything; its own check says what is wrong.
    found = (
        (e.get("id") for e in entries if isinstance(e, dict)) if isinstance(entries, list) else ()
    )
    root = Path(addons_root) if addons_root is not None else addons_dir()
    held = {RESERVED_NAMESPACE, *held_namespaces(config_dir, root)}
    return [i for i in found if isinstance(i, str) and i and i not in held]


def _launch_problem(addon: InstalledAddon) -> str | None:
    """Why `addon`'s store cannot be read at this launch: its manifest, and
    its `profiles.yaml`, which every launch's profile table reads, checked
    against the digest the store was written with. An edited store leaves
    only this add-on out, so only a launch that reads from it stops. Skills
    are checked where they are read (`skill.read`)."""
    from kraft.addons import fetch

    if not addon.root.is_dir():
        return None  # the load checks say why
    try:
        fetch.open_store(addon.root).read("profiles.yaml", optional=True)
    except fetch.StoreMismatch as exc:
        return str(exc)
    except OSError as exc:
        return f"its store cannot be read: {exc}"
    return None


#: Why the last restore of a store failed, by its digest: what a launch that
#: needed it says when it stops. Cleared when a restore of it succeeds.
_unrestored: dict[str, str] = {}


class RestoreError(Exception):
    """A store that is gone and cannot be put back. The message says why and,
    where there is one, what to run."""


def restore(
    addon: InstalledAddon, config_dir: Path | None = None, addons_root: Path | None = None
) -> Path:
    """Put `addon`'s missing store back, from where the lock (or a work
    item's pin) says its files came from, and return it. Not a review: this
    commit was accepted when it was locked, and it is taken only if its tree
    and digest are still the ones recorded. A directory collection is copied
    again only while the folder still matches the digest. Raises `RestoreError`."""
    from kraft import paths

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    root = Path(addons_root) if addons_root is not None else addons_dir()
    if addon.root.is_dir():
        return addon.root
    try:
        store = _restore(addon, config_dir, root)
    except RestoreError as exc:
        _unrestored[addon.root.name] = str(exc)
        raise
    _unrestored.pop(addon.root.name, None)
    return store


def _restore(addon: InstalledAddon, config_dir: Path, root: Path) -> Path:
    from kraft.addons import fetch, update
    from kraft.addons.config import AddonsConfig
    from kraft.config import ConfigError

    if addon.repo_id is not None:
        return _restore_repo_library(addon, config_dir, root)
    what = f"{addon.id} {addon.version}"
    collection_name = addon.id.split("@", 1)[1]
    try:
        collection = AddonsConfig.load(config_dir / AddonsConfig.FILE).collections.get(
            collection_name
        )
    except ConfigError as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if collection is None:
        raise RestoreError(
            f"{what}: collection {collection_name} is no longer in addons.yaml; add it again"
        )
    again = f"run `kraft admin addon install {addon.id} --re-install`"
    if addon.source is None:
        raise RestoreError(f"{what}: its pin does not record where its files came from; {again}")
    try:
        if collection.git is not None and addon.commit is not None:
            try:
                mirror = fetch.fetch_commit(root, collection_name, collection.git, addon.commit)
            except fetch.FetchError as exc:
                raise RestoreError(
                    f"{what}: locked commit {addon.commit[:12]} could not be fetched "
                    f"({exc.kind}: {exc}); if it is no longer on the remote, {again}"
                ) from exc
            fetch.pin(mirror, addon.commit)
            # A commit names its tree: only the digest, which also covers how
            # this Kraft extracts, is left to check.
            extracted = fetch.extract_git(mirror, addon.commit, addon.source)
        elif collection.path is not None and addon.commit is None:
            extracted = fetch.extract_dir(Path(collection.path) / addon.source.removeprefix("./"))
        else:
            raise RestoreError(
                f"{what}: collection {collection_name} is no longer the kind it was locked "
                f"from; {again}"
            )
    except (OSError, fetch.FetchError, fetch.AddonRefused) as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if fetch.digest(extracted.files) != f"sha256:{addon.root.name}":
        raise RestoreError(
            f"{what}: its files no longer match the locked digest; "
            + (again if addon.commit else "run `kraft admin addon update` to review the change")
        )
    try:
        with update.write_lock(root):
            return fetch.write_store(root, extracted)
    except (update.Busy, OSError) as exc:  # a full disk or a read-only run directory
        raise RestoreError(f"{what}: {exc}") from exc


def _restore_repo_library(addon: InstalledAddon, config_dir: Path, root: Path) -> Path:
    from kraft.addons import fetch, repo_library, update
    from kraft.config import ConfigError, load_repos

    what = f"repo library {addon.repo_id}"
    try:
        entry = next(
            (r for r in load_repos(config_dir / "repos.yaml") if r.id == addon.repo_id), None
        )
    except ConfigError as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if entry is None or addon.commit is None:
        raise RestoreError(f"{what}: its repository is no longer connected")
    try:
        extracted = repo_library.extract(Path(entry.path), addon.commit)
    except (OSError, fetch.FetchError, fetch.AddonRefused) as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if extracted is None or fetch.digest(extracted.files) != f"sha256:{addon.root.name}":
        raise RestoreError(
            f"{what}: commit {addon.commit[:12]} no longer gives the accepted files; "
            "run `kraft repo library`"
        )
    try:
        with update.write_lock(root):
            return fetch.write_store(root, extracted)
    except (update.Busy, OSError) as exc:
        raise RestoreError(f"{what}: {exc}") from exc


def restore_missing(
    addons: tuple[InstalledAddon, ...],
    config_dir: Path | None = None,
    addons_root: Path | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, str]:
    """Restore every store of `addons` that is gone; add-on id to why, for
    each that could not be. An add-on left out on purpose is not restored.
    `cancel` (set at shutdown) is checked before each: the rest are reported
    `cancelled`, and a restore already running finishes, its write atomic."""
    failed: dict[str, str] = {}
    for addon in addons:
        if addon.quiet:
            continue
        if cancel is not None and cancel.is_set():
            failed[addon.id] = "cancelled"
            continue
        try:
            restore(addon, config_dir, addons_root)
        except RestoreError as exc:
            failed[addon.id] = str(exc)
    return failed


def gc(keep: Callable[[], Collection[str]], addons_root: Path | None = None) -> list[str]:
    """Delete every extracted add-on whose digest is not in `keep()` (hex, as
    a store directory is named), and whatever an interrupted write left in
    `staging/`. `keep` is asked under the update lock, so a store an install
    finished a moment ago is already in its answer; a lock someone else holds
    is not waited for (raises `update.Busy`): the next collection takes what
    this one left. Returns the digests deleted. Only the daemon calls this:
    it alone knows what its loaded library and its unfinished work items read."""
    from kraft.addons import fetch, update

    root = Path(addons_root) if addons_root is not None else addons_dir()
    removed: list[str] = []
    with update.write_lock(root, wait=0):
        kept = keep()
        try:
            fetch.remove_tree(root / "staging")  # a replaced store's copy is read-only
        except OSError:
            pass  # gone already, or the next collection takes it
        stores = sorted((root / "store").iterdir()) if (root / "store").is_dir() else []
        for store in stores:
            if store.name in kept:
                continue
            fetch.remove_tree(store)
            removed.append(store.name)
    return removed


def left_out_by(version: str, config_dir: Path | None = None) -> list[str]:
    """Each loaded add-on a Kraft `version` would leave out, with why: its
    `requires.kraft` names another major. What `kraft admin update` lists
    before it installs a release."""
    from kraft.addons import fetch, manifest

    out = []
    for addon in installed(config_dir):
        if addon.left_out is not None or addon.repo_id is not None:
            continue
        try:
            text = (fetch.open_store(addon.root).read(manifest.ADDON_JSON) or b"").decode()
            declared = manifest.addon(
                manifest.parse(text, manifest.ADDON_JSON), manifest.ADDON_JSON
            )
        except (OSError, ValueError, fetch.AddonRefused, manifest.ManifestError):
            continue
        if why := manifest.kraft_compatible(declared.requires.kraft, version):
            out.append(f"{addon.id} {addon.version} {why}")
    return out
