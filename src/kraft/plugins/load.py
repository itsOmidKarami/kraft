"""An installed Kraft plugin as the template library and the agent-profile
table read it.

A leaf module on purpose: it imports nothing from `kraft.templates` or
`kraft.config`. `kraft.config` imports `kraft.templates.environment`, which
reads plugins, so an import the other way would be a cycle.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class InstalledPlugin:
    """One plugin the instance loads: where its extracted files are and the
    namespace everything it declares is addressed under."""

    #: `<plugin>@<collection>`.
    id: str
    #: The name its `plugin.json` declares.
    name: str
    #: `name`, or the alias it was installed under.
    namespace: str
    version: str
    #: Its extracted files, in the fixed layout: `library.yaml`, `chains/`,
    #: `skills/`, `profiles.yaml`.
    root: Path
    #: Who sees it. Repo collections will add a second value.
    scope: Literal["instance"] = "instance"
    #: Why this plugin is known to the instance but not loaded; None when it loads.
    left_out: str | None = None
    #: A plugin left out on purpose (disabled, not yet installed, orphaned in
    #: the lock) is no fault: health does not report it.
    quiet: bool = False
    #: Where the lock says its files came from: the collection's commit, the
    #: plugin's directory in it and that directory's tree. None for a
    #: directory collection, and for a plugin that is not locked.
    commit: str | None = None
    tree: str | None = None
    source: str | None = None

    @property
    def pin(self) -> dict[str, str | None]:
        """What a work item records of this plugin: enough to find its store,
        and to restore it from its commit when the store is gone."""
        return {
            "id": self.id,
            "commit": self.commit,
            "tree": self.tree,
            "source": self.source,
            "digest": f"sha256:{self.root.name}",
            "version": self.version,
        }


def _reference(value: str, plugin: InstalledPlugin, *, bare_is_own: bool) -> str:
    qualifier, colon, name = value.partition(":")
    if not colon:
        return f"{plugin.namespace}:{value}" if bare_is_own else value
    return f"{plugin.namespace}:{name}" if qualifier == plugin.name else value


def qualify(data: object, plugin: InstalledPlugin) -> object:
    """`data`, a plugin's parsed YAML, with its references made absolute.

    A bare `extends:`, `steering:` or `skill:` names the plugin's own
    declaration, and so does one qualified by the plugin's own name; both
    become `<namespace>:<name>`, so an aliased plugin works unchanged. A bare
    `profile:` is the instance's agent profile and stays bare; only the
    plugin's own name in it is rewritten. Any other qualifier (`kraft:`,
    another tool's plugin) and every `harness:` is left as written.
    """
    if isinstance(data, list):
        return [qualify(item, plugin) for item in data]
    if not isinstance(data, Mapping):
        return data
    out: dict[object, object] = {}
    for key, value in data.items():
        if key in ("extends", "skill") and isinstance(value, str):
            out[key] = _reference(value, plugin, bare_is_own=True)
        elif key == "steering" and isinstance(value, list):
            out[key] = [
                _reference(v, plugin, bare_is_own=True) if isinstance(v, str) else v for v in value
            ]
        elif key == "profile" and isinstance(value, str):
            out[key] = _reference(value, plugin, bare_is_own=False)
        else:
            out[key] = qualify(value, plugin)
    return out


def plugins_dir() -> Path:
    """This process's `run/plugins`: `KRAFT_RUN_DIR`, else the default run directory."""
    from kraft.paths import RunDirs, default_run_dir

    return RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).plugins


def config_problem(config_dir: Path) -> str | None:
    """Why `plugins.yaml` or `plugins.lock` in `config_dir` cannot be read, or
    None. While one cannot, no plugin loads."""
    from kraft.config import ConfigError
    from kraft.plugins.config import PluginsConfig, PluginsLock

    try:
        PluginsConfig.load(Path(config_dir) / PluginsConfig.FILE)
        PluginsLock.load(Path(config_dir) / PluginsLock.FILE)
    except ConfigError as exc:
        return str(exc)
    return None


def installed(
    config_dir: Path | None = None,
    plugins_root: Path | None = None,
    *,
    verify: bool = False,
    config=None,
) -> tuple[InstalledPlugin, ...]:
    """Every plugin `plugins.yaml` or `plugins.lock` names, each either
    loadable (`left_out` None) or carrying why it is not.

    A plugin loads when it has an enabled `plugins.yaml` entry and a lock
    entry, from the store the lock names and under the namespace the lock
    recorded: an alias changed by hand waits for an update. Nothing is fetched
    here, ever. `verify` re-hashes every stored file, not only the manifest.
    `config` is a `PluginsConfig` to read in place of the file: an edit of
    `plugins.yaml` not saved yet.
    """
    from kraft import paths
    from kraft.config import ConfigError
    from kraft.plugins.config import PluginsConfig, PluginsLock

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    store = (Path(plugins_root) if plugins_root is not None else plugins_dir()) / "store"
    try:
        config = config or PluginsConfig.load(config_dir / PluginsConfig.FILE)
        lock = PluginsLock.load(config_dir / PluginsLock.FILE)
    except ConfigError:
        return ()  # `config_problem` says why
    found: list[InstalledPlugin] = []
    for plugin_id in config.plugins:
        name = plugin_id.split("@", 1)[0]
        locked = lock.plugins.get(plugin_id)
        if locked is None:
            found.append(
                InstalledPlugin(
                    plugin_id,
                    name,
                    config.namespace(plugin_id),
                    "",
                    store / "not-installed",
                    left_out="is not installed; run `kraft admin plugin install`",
                    quiet=True,
                )
            )
            continue
        plugin = InstalledPlugin(
            plugin_id,
            name,
            locked.namespace,
            locked.version,
            store / locked.digest.removeprefix("sha256:"),
            commit=locked.commit,
            tree=locked.tree,
            source=locked.source,
        )
        if not config.entry(plugin_id).enabled:
            found.append(replace(plugin, left_out="is disabled", quiet=True))
        else:
            found.append(replace(plugin, left_out=_why_left_out(plugin, config_dir, verify)))
    for plugin_id, locked in lock.plugins.items():
        if plugin_id not in config.plugins:
            found.append(
                InstalledPlugin(
                    plugin_id,
                    plugin_id.split("@", 1)[0],
                    locked.namespace,
                    locked.version,
                    store / locked.digest.removeprefix("sha256:"),
                    left_out="is locked but not listed in plugins.yaml",
                    quiet=True,
                )
            )
    return tuple(found)


def _why_left_out(plugin: InstalledPlugin, config_dir: Path, verify: bool) -> str | None:
    """The first load-time check `plugin` fails, as a reason; None when it loads."""
    from kraft import harness, update
    from kraft.config import ConfigError, read_yaml
    from kraft.plugins import fetch, manifest
    from kraft.policy import PolicyError, PolicyInput
    from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    root = plugin.root
    if not root.is_dir():
        if why := _unrestored.get(root.name):
            return f"its store could not be restored: {why}"
        return "its store is missing and has not been restored"
    mismatch = "its files do not match the locked digest"
    try:
        recorded = (root / fetch.DIGEST_FILE).read_bytes()
        if hashlib.sha256(recorded).hexdigest() != root.name:
            return mismatch
        if verify and fetch.digest_manifest(fetch.extract_dir(root).files) != recorded:
            return mismatch
        declared = manifest.plugin(
            manifest.parse((root / manifest.PLUGIN_JSON).read_text(), manifest.PLUGIN_JSON),
            manifest.PLUGIN_JSON,
        )
    except (OSError, ValueError, fetch.PluginRefused, manifest.ManifestError) as exc:
        return f"its store cannot be read: {exc}"
    running = update.installed()
    if not manifest.is_source_build(running):
        if why := manifest.kraft_compatible(declared.requires.kraft, running):
            return why
    if why := instance_problem(plugin.namespace, declared.requires, config_dir):
        return why
    try:
        harnesses = read_yaml(config_dir / "harnesses.yaml")
    except ConfigError:
        return None  # that file's own check says why
    # Its own files, read alone: a plugin that does not load must not take the
    # library or the profile table down with it.
    try:
        TemplateLibrary.from_mappings(
            {}, (), library_path=config_dir / "library.yaml", plugins=[plugin]
        )
        HarnessProfileTable.from_mapping(
            harnesses,
            config_dir / "harnesses.yaml",
            harnesses=harness.load(None).valid,
            plugins=[plugin],
        )
    except (TemplateLibraryError, TemplateEnvironmentError) as exc:
        if str(root) in str(exc) or f"profiles.{plugin.namespace}:" in str(exc):
            return str(exc)
        return None
    try:
        policy = PolicyInput.from_yaml(config_dir / "policy.yaml").instance_policy()
    except PolicyError:
        return None  # no policy.yaml, or one whose own check says why
    return limits_problem(plugin, policy)


def limits_problem(plugin: InstalledPlugin, instance_policy) -> str | None:
    """Why one of `plugin`'s limits no longer fits under `instance_policy`'s
    `maxima:`, lowered since the plugin was installed; None when all fit."""
    from kraft.templates.library import TemplateLibrary, TemplateLibraryError

    try:
        library = TemplateLibrary.from_mappings(
            {}, (), library_path=plugin.root / "local-library.yaml", plugins=[plugin]
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
) -> str | None:
    """Why this instance cannot hold a plugin under `namespace` that declares
    `requires` (its manifest's): a harness or agent profile `harnesses.yaml`
    does not define, or a repository whose id is the namespace. None when it
    can. Asked at load and of every install or update candidate. `harnesses`
    is a `harnesses.yaml` to judge in place of the file, `repos` likewise a
    `repos.yaml`: an edit not saved yet."""
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
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict) and entry.get("id") == namespace:
            return f"its namespace {namespace!r} is now a repository id"
    return None


def for_item(
    pins: Mapping[str, Mapping[str, str | None]] | None,
    config_dir: Path | None = None,
    plugins_root: Path | None = None,
) -> tuple[InstalledPlugin, ...]:
    """The plugins one work item reads: the versions its chain was
    materialized with (`pins`, namespace to `InstalledPlugin.pin`), and the
    lock's for every namespace it did not pin. A pinned version whose store
    is gone is known and not loaded: its skills are refused, never handed to
    the agent as another tool's."""
    store = (Path(plugins_root) if plugins_root is not None else plugins_dir()) / "store"
    pinned = []
    for namespace, pin in (pins or {}).items():
        plugin_id, digest = str(pin["id"]), str(pin["digest"])
        root = store / digest.removeprefix("sha256:")
        pinned.append(
            InstalledPlugin(
                plugin_id,
                plugin_id.split("@", 1)[0],
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
            )
        )
    live = [p for p in installed(config_dir, plugins_root) if p.namespace not in (pins or {})]
    return (*live, *pinned)


#: Why the last restore of a store failed, by its digest: what a launch that
#: needed it says when it stops. Cleared when a restore of it succeeds.
_unrestored: dict[str, str] = {}


class RestoreError(Exception):
    """A store that is gone and cannot be put back. The message says why and,
    where there is one, what to run."""


def restore(
    plugin: InstalledPlugin, config_dir: Path | None = None, plugins_root: Path | None = None
) -> Path:
    """Put `plugin`'s missing store back, from where the lock (or a work
    item's pin) says its files came from, and return it. Not a review: this
    commit was accepted when it was locked, and it is taken only if its tree
    and digest are still the ones recorded. A directory collection is copied
    again only while the folder still matches the digest. Raises `RestoreError`."""
    from kraft import paths

    config_dir = Path(config_dir) if config_dir is not None else paths.config_dir()
    root = Path(plugins_root) if plugins_root is not None else plugins_dir()
    if plugin.root.is_dir():
        return plugin.root
    try:
        store = _restore(plugin, config_dir, root)
    except RestoreError as exc:
        _unrestored[plugin.root.name] = str(exc)
        raise
    _unrestored.pop(plugin.root.name, None)
    return store


def _restore(plugin: InstalledPlugin, config_dir: Path, root: Path) -> Path:
    from kraft.config import ConfigError
    from kraft.plugins import fetch, update
    from kraft.plugins.config import PluginsConfig

    what = f"{plugin.id} {plugin.version}"
    collection_name = plugin.id.split("@", 1)[1]
    try:
        collection = PluginsConfig.load(config_dir / PluginsConfig.FILE).collections.get(
            collection_name
        )
    except ConfigError as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if collection is None or plugin.source is None:
        raise RestoreError(
            f"{what}: collection {collection_name} is no longer in plugins.yaml; add it again"
        )
    again = f"run `kraft admin plugin install {plugin.id} --re-install`"
    try:
        if collection.git is not None and plugin.commit is not None:
            try:
                mirror = fetch.fetch_commit(root, collection_name, collection.git, plugin.commit)
            except fetch.FetchError as exc:
                raise RestoreError(
                    f"{what}: locked commit {plugin.commit[:12]} could not be fetched "
                    f"({exc.kind}: {exc}); if it is no longer on the remote, {again}"
                ) from exc
            fetch.pin(mirror, plugin.commit)
            # A commit names its tree: only the digest, which also covers how
            # this Kraft extracts, is left to check.
            extracted = fetch.extract_git(mirror, plugin.commit, plugin.source)
        elif collection.path is not None and plugin.commit is None:
            extracted = fetch.extract_dir(Path(collection.path) / plugin.source.removeprefix("./"))
        else:
            raise RestoreError(
                f"{what}: collection {collection_name} is no longer the kind it was locked "
                f"from; {again}"
            )
    except (OSError, fetch.FetchError, fetch.PluginRefused) as exc:
        raise RestoreError(f"{what}: {exc}") from exc
    if fetch.digest(extracted.files) != f"sha256:{plugin.root.name}":
        raise RestoreError(
            f"{what}: its files no longer match the locked digest; "
            + (again if plugin.commit else "run `kraft admin plugin update` to review the change")
        )
    try:
        with update.write_lock(root):
            return fetch.write_store(root, extracted)
    except (update.Busy, OSError) as exc:  # a full disk or a read-only run directory
        raise RestoreError(f"{what}: {exc}") from exc


def restore_missing(
    plugins: tuple[InstalledPlugin, ...],
    config_dir: Path | None = None,
    plugins_root: Path | None = None,
) -> dict[str, str]:
    """Restore every store of `plugins` that is gone; plugin id to why, for
    each that could not be. A plugin left out on purpose is not restored."""
    failed: dict[str, str] = {}
    for plugin in plugins:
        if plugin.quiet:
            continue
        try:
            restore(plugin, config_dir, plugins_root)
        except RestoreError as exc:
            failed[plugin.id] = str(exc)
    return failed


def gc(keep: Callable[[], Collection[str]], plugins_root: Path | None = None) -> list[str]:
    """Delete every extracted plugin whose digest is not in `keep()` (hex, as
    a store directory is named), and whatever an interrupted write left in
    `staging/`. `keep` is asked under the update lock, so a store an install
    finished a moment ago is already in its answer; a lock someone else holds
    is not waited for (raises `update.Busy`): the next collection takes what
    this one left. Returns the digests deleted. Only the daemon calls this:
    it alone knows what its loaded library and its unfinished work items read."""
    import shutil

    from kraft.plugins import update

    root = Path(plugins_root) if plugins_root is not None else plugins_dir()
    removed: list[str] = []
    with update.write_lock(root, wait=0):
        kept = keep()
        shutil.rmtree(root / "staging", ignore_errors=True)
        stores = sorted((root / "store").iterdir()) if (root / "store").is_dir() else []
        for store in stores:
            if store.name in kept:
                continue
            for path in [store, *store.rglob("*")]:
                if path.is_dir():
                    path.chmod(0o755)  # a store is read-only
            shutil.rmtree(store)
            removed.append(store.name)
    return removed


def left_out_by(version: str, config_dir: Path | None = None) -> list[str]:
    """Each loaded plugin a Kraft `version` would leave out, with why: its
    `requires.kraft` names another major. What `kraft admin update` lists
    before it installs a release."""
    from kraft.plugins import manifest

    out = []
    for plugin in installed(config_dir):
        if plugin.left_out is not None:
            continue
        try:
            declared = manifest.plugin(
                manifest.parse(
                    (plugin.root / manifest.PLUGIN_JSON).read_text(), manifest.PLUGIN_JSON
                ),
                manifest.PLUGIN_JSON,
            )
        except (OSError, ValueError, manifest.ManifestError):
            continue
        if why := manifest.kraft_compatible(declared.requires.kraft, version):
            out.append(f"{plugin.id} {plugin.version} {why}")
    return out
