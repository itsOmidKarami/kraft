"""An installed Kraft plugin as the template library and the agent-profile
table read it.

A leaf module on purpose: it imports nothing from `kraft.templates` or
`kraft.config`. `kraft.config` imports `kraft.templates.environment`, which
reads plugins, so an import the other way would be a cycle.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
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
