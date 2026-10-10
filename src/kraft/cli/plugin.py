"""`kraft admin plugin`: Kraft plugins and collections.

The verbs edit `plugins.yaml` and run `kraft.plugins.update.update`; each one
that changes what loads asks a running server to rebuild its library from the
new lock. A worker runs none of the ones that write.

`validate` runs every check an install would: extraction under the limits,
the manifests, the Kraft version, and a lint, against this instance when
there is a Kraft home and of each plugin on its own when there is none. It
installs nothing and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from kraft import client, harness, paths, update
from kraft.cli import common
from kraft.config import ConfigError, first_error, read_yaml, write_yaml
from kraft.plugins import fetch, load, manifest, review
from kraft.plugins import update as plugin_update
from kraft.plugins.config import PluginEntry, PluginsConfig, PluginsLock
from kraft.plugins.load import InstalledPlugin
from kraft.policy import PolicyError
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

#: What only an instance can check: run when a Kraft home exists.
_NEEDS_AN_INSTANCE = (
    "no Kraft home was used, so these were skipped: requires.harnesses and requires.profiles, "
    "namespace clashes, and lint against an instance's own library"
)


def _lint(found: manifest.PluginManifest, extracted: fetch.Extracted, root: Path) -> list[dict]:
    """Every chain of the plugin that does not resolve, with the plugin as the
    only layer of an otherwise empty library."""
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp)
        for rel, (_mode, data) in extracted.files.items():
            (store / rel).parent.mkdir(parents=True, exist_ok=True)
            (store / rel).write_bytes(data)
        plugin = InstalledPlugin(
            id=f"{found.name}@validate",
            name=found.name,
            namespace=found.name,
            version=found.version,
            root=store,
        )
        try:
            library = TemplateLibrary.from_mappings(
                {}, (), library_path=store / "local-library.yaml", plugins=[plugin]
            )
            messages = [issue.message for issue in library.lint()]
            HarnessProfileTable.from_mapping(
                {}, store / "harnesses.yaml", harnesses=harness.load(None).valid, plugins=[plugin]
            )
        except (TemplateLibraryError, TemplateEnvironmentError) as exc:
            messages = [str(exc)]
        # The author's own path, not the temporary copy's.
        return [{"file": str(root), "message": m.replace(tmp, str(root))} for m in messages]


def _lint_in(
    found: manifest.PluginManifest,
    extracted: fetch.Extracted,
    root: Path,
    collection: str | None,
    home: tuple[Path, Path],
) -> list[dict]:
    """What installing the plugin into the instance at `home` would be refused
    for: an unmet `requires`, a namespace another plugin holds, and anything
    of the instance's own that would stop resolving. The same plugin already
    installed is replaced, not a clash."""
    config_dir, plugins_dir = home

    def same(plugin_id: str) -> bool:
        name, _, where = plugin_id.partition("@")
        return name == found.name and collection in (None, where)

    now = load.installed(config_dir, plugins_dir)
    loaded = [p for p in now if not same(p.id)]
    held = next((p.id for p in loaded if p.namespace == found.name), None)
    if held is not None:
        return [{"file": str(root), "message": f"namespace {found.name!r} is taken by {held}"}]
    if why := load.instance_problem(found.name, found.requires, config_dir):
        return [{"file": str(root), "message": why}]
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp)
        for rel, (_mode, data) in extracted.files.items():
            (store / rel).parent.mkdir(parents=True, exist_ok=True)
            (store / rel).write_bytes(data)
        candidate = InstalledPlugin(
            f"{found.name}@{collection or 'validate'}", found.name, found.name, found.version, store
        )
        try:
            broken = plugin_update.breaks(
                plugin_update.state_of(config_dir, now),
                plugin_update.state_of(config_dir, [*loaded, candidate]),
                config_dir,
                found.name,
            )
        except PolicyError as exc:
            broken = [str(exc)]
        return [{"file": str(root), "message": m.replace(tmp, str(root))} for m in broken]


def validate(path: Path, home: tuple[Path, Path] | None = None) -> dict:
    """`{plugins, problems, warnings, skipped}` for the collection or the one
    plugin directory at `path`. With `home`, a Kraft home's config and plugins
    directories, each plugin is also judged as an install into that instance."""
    report: dict = {
        "plugins": [],
        "problems": [],
        "warnings": [],
        "skipped": [] if home else [_NEEDS_AN_INSTANCE],
    }
    collection_name = None

    def problem(file: Path, message: str) -> None:
        # A manifest error already starts with the file it is about, which can
        # be one inside `file`: that file is the one to name, once.
        head, sep, rest = message.partition(": ")
        if sep and head.startswith(str(file)):
            file, message = Path(head), rest
        report["problems"].append({"file": str(file), "message": message})

    collection_file = path / manifest.COLLECTION_JSON
    if collection_file.is_file():
        try:
            raw = manifest.parse(collection_file.read_text(), str(collection_file))
            listed = manifest.collection(raw, str(collection_file))
        except (OSError, ValueError, manifest.ManifestError) as exc:
            problem(collection_file, str(exc))
            return report
        report["warnings"] += [
            f"{collection_file}: '{key}' is not read by Kraft"
            for key in manifest.unknown_keys(raw, manifest.CollectionManifest)
        ]
        targets = [(entry.name, path / entry.source.removeprefix("./")) for entry in listed.plugins]
        collection_name = listed.name
    elif (path / manifest.PLUGIN_JSON).is_file():
        targets = [(None, path)]
    else:
        problem(path, f"holds neither {manifest.COLLECTION_JSON} nor {manifest.PLUGIN_JSON}")
        return report

    running = update.installed()
    if manifest.is_source_build(running):
        report["skipped"].append(f"requires.kraft: this Kraft ({running}) is a source build")
    for entry, root in targets:
        plugin_file = root / manifest.PLUGIN_JSON
        try:
            extracted = fetch.extract_dir(root)
            if manifest.PLUGIN_JSON not in extracted.files:
                raise manifest.ManifestError(f"{root}: no {manifest.PLUGIN_JSON}")
            raw = manifest.parse(
                extracted.files[manifest.PLUGIN_JSON][1].decode(), str(plugin_file)
            )
            found = manifest.plugin(raw, str(plugin_file))
            manifest.check_plugin(found, entry, extracted.files)
            others = {p.namespace for p in load.installed(*home)} if home else set()
            plugin_update.check(found, extracted.files, other_namespaces=others - {found.name})
        except plugin_update.Refused as exc:
            for why in exc.problems:
                problem(root, why)
            continue
        except (fetch.PluginRefused, manifest.ManifestError) as exc:
            problem(root, str(exc))
            continue
        report["warnings"] += [
            f"{plugin_file}: '{key}' is not read by Kraft"
            for key in manifest.unknown_keys(raw, manifest.PluginManifest)
        ]
        report["warnings"] += [
            f"{root}/{rel} is not part of the plugin layout and is ignored"
            for rel in extracted.skipped
        ]
        why = manifest.kraft_compatible(found.requires.kraft, running)
        if why is not None:
            problem(plugin_file, f"{found.name} {found.version} {why}; this is Kraft {running}")
            continue
        problems = (
            _lint_in(found, extracted, root, collection_name, home)
            if home
            else _lint(found, extracted, root)
        )
        report["problems"] += problems
        report["plugins"].append(
            {"name": found.name, "version": found.version, "digest": fetch.digest(extracted.files)}
        )
    return report


def _render(report: dict) -> str:
    lines = [f"{p['name']} {p['version']}  {p['digest']}" for p in report["plugins"]]
    lines += [f"error: {p['file']}: {p['message']}" for p in report["problems"]]
    lines += [f"warning: {w}" for w in report["warnings"]]
    lines += [f"skipped: {s}" for s in report["skipped"]]
    return "\n".join(lines)


def _cmd_validate(ns: argparse.Namespace) -> None:
    config_dir, plugins_dir = _home()
    # A Kraft home is one that has a library; CI has none.
    home = (config_dir, plugins_dir) if (config_dir / "library.yaml").is_file() else None
    report = validate(Path(ns.path).resolve(), home)
    common.emit(report, _render, ns.json)
    if report["problems"]:
        raise SystemExit(1)


#: GitHub's `owner/repo`, which `collection add` writes as a full URL.
_OWNER_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")


def _home() -> tuple[Path, Path]:
    return paths.config_dir(), load.plugins_dir()


def _refuse(message: str) -> SystemExit:
    return SystemExit(f"error: {fetch.redact(message)}")


def _not_a_worker() -> None:
    """Like `forbid_self_action`, consistency and not a security boundary: a
    worker does not change the instance it runs on."""
    if os.environ.get("KRAFT_WORK_ITEM_ID"):
        raise _refuse("a worker does not change the instance's plugins")


def _reload() -> None:
    """Ask a running server to load the new lock. With none running, the next
    start reads it."""
    try:
        answer = asyncio.run(client.reload_plugins())
    except ValueError as exc:
        if "no Kraft server" not in str(exc):
            print(f"warning: the running server did not reload: {exc}", file=sys.stderr)
        return
    # A reload that answers 200 can still have left a plugin out.
    for what, why in ((answer or {}).get("invalid_templates") or {}).items():
        if what == "plugins.yaml" or what.startswith("plugin "):
            print(f"warning: the running server did not load {what}: {why}", file=sys.stderr)


def _edit(
    change: Callable[[dict, PluginsConfig], None], then: Callable[[], None] | None = None
) -> PluginsConfig:
    """`plugins.yaml` rewritten by `change(raw, parsed)` under the update lock,
    refused when the result is not a valid file. `then` runs after the write,
    still under the lock."""
    config_dir, plugins_dir = _home()
    path = config_dir / PluginsConfig.FILE
    try:
        with plugin_update.write_lock(plugins_dir):
            raw = read_yaml(path, {})
            for section in ("collections", "plugins"):
                raw[section] = raw.get(section) or {}
            change(raw, PluginsConfig.load(path))
            try:
                config = PluginsConfig.model_validate(raw)
            except ValidationError as exc:
                raise _refuse(first_error(exc, PluginsConfig.FILE)) from exc
            write_yaml(path, raw)
            if then is not None:
                then()
            return config
    except (ConfigError, plugin_update.Busy) as exc:
        raise _refuse(str(exc)) from exc


def _installed_id(config: PluginsConfig, plugin_id: str) -> None:
    if plugin_id not in config.plugins:
        raise _refuse(f"{plugin_id} is not installed")


def _long_entry(raw: dict, plugin_id: str) -> dict:
    """The entry as a map, whichever form the file wrote it in."""
    entry = raw["plugins"][plugin_id]
    raw["plugins"][plugin_id] = entry = {"enabled": entry} if isinstance(entry, bool) else entry
    return entry


def _short_entry(raw: dict, plugin_id: str) -> None:
    """`true`/`false` again when nothing else is set."""
    entry = raw["plugins"][plugin_id]
    if set(entry) <= {"enabled"}:
        raw["plugins"][plugin_id] = entry.get("enabled", True)
    elif entry.get("enabled") is True:
        del entry["enabled"]


def _catalogue(name: str, collection, plugins_dir: Path) -> list[str] | None:
    """The plugins the collection listed when it was last fetched; None when
    it has not been. No network."""
    where = manifest.COLLECTION_JSON
    try:
        if collection.git is None:
            text = (Path(collection.path) / where).read_text()
        else:
            mirror = fetch.mirror_path(plugins_dir, name, collection.git)
            if not mirror.is_dir():
                return None
            commit = fetch._git("rev-parse", "--verify", "FETCH_HEAD^{commit}", git_dir=mirror)
            text = fetch.read_file(mirror, commit.decode().strip(), where, fetch.MAX_JSON).decode()
        return [
            entry.name for entry in manifest.collection(manifest.parse(text, where), where).plugins
        ]
    except (OSError, ValueError, fetch.FetchError, fetch.PluginRefused, manifest.ManifestError):
        return None


def _cmd_collection_add(ns: argparse.Namespace) -> None:
    _not_a_worker()
    _config_dir, plugins_dir = _home()
    where = manifest.COLLECTION_JSON
    directory = Path(ns.source).expanduser()
    try:
        if directory.is_dir():
            source = {"path": str(directory.resolve())}
            if ns.ref:
                raise _refuse("--ref is for a git collection; a directory is read as it is")
            text = (directory / where).read_text()
            mirror = None
        else:
            url = (
                f"https://github.com/{ns.source}.git" if _OWNER_REPO.match(ns.source) else ns.source
            )
            source = {"git": url, **({"ref": ns.ref} if ns.ref else {})}
            mirror, commit = fetch.fetch(plugins_dir, "adding", url, ns.ref)
            text = fetch.read_file(mirror, commit, where, fetch.MAX_JSON).decode()
        name = manifest.collection(manifest.parse(text, where), where).name
    except (
        OSError,
        ValueError,
        fetch.FetchError,
        fetch.PluginRefused,
        manifest.ManifestError,
    ) as exc:
        raise _refuse(str(exc)) from exc

    def change(raw: dict, config: PluginsConfig) -> None:
        known = config.collections.get(name)
        if known is not None and (known.git, known.path) != (source.get("git"), source.get("path")):
            raise _refuse(
                f"collection {name} is already added from {known.git or known.path}; "
                "remove it first to add it from another source"
            )
        entry = {**(raw["collections"].get(name) or {}), **source}
        if ns.auto_update:
            entry["auto_update"] = True
        raw["collections"][name] = entry

    try:
        _edit(change)
        if mirror is not None:
            final = fetch.mirror_path(plugins_dir, name, source["git"])
            if not final.is_dir():
                mirror.rename(final)
    finally:
        # What was fetched to read the name, when the collection was not added
        # or already had its mirror.
        if mirror is not None:
            shutil.rmtree(mirror, ignore_errors=True)
    shown = {**source, "git": fetch.redact(source["git"])} if "git" in source else source
    common.emit({"collection": name, **shown}, lambda v: f"added collection {name}", ns.json)


def _cmd_collection_list(ns: argparse.Namespace) -> None:
    config_dir, plugins_dir = _home()
    try:
        config = PluginsConfig.load(config_dir / PluginsConfig.FILE)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    rows = []
    for name, collection in config.collections.items():
        listed = _catalogue(name, collection, plugins_dir)
        rows.append(
            {
                "name": name,
                "source": fetch.redact(collection.git) if collection.git else collection.path,
                "ref": collection.ref,
                "auto_update": collection.auto_update,
                "plugins": None
                if listed is None
                else [{"name": p, "installed": f"{p}@{name}" in config.plugins} for p in listed],
            }
        )

    def render(value: list[dict]) -> str:
        lines = []
        for row in value:
            at = f" @ {row['ref']}" if row["ref"] else ""
            lines.append(f"{row['name']}  {row['source']}{at}")
            if row["plugins"] is None:
                lines.append("  not fetched yet; run `kraft admin plugin collection update`")
            for plugin in row["plugins"] or ():
                lines.append(f"  {plugin['name']}{'  (installed)' if plugin['installed'] else ''}")
        return "\n".join(lines) or "no collections"

    common.emit(rows, render, ns.json)


def _cmd_collection_update(ns: argparse.Namespace) -> None:
    config_dir, plugins_dir = _home()
    try:
        config = PluginsConfig.load(config_dir / PluginsConfig.FILE)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    if ns.name and ns.name not in config.collections:
        raise _refuse(f"no collection {ns.name}")
    done, failed = [], []
    for name, collection in config.collections.items():
        if collection.git is None or (ns.name and name != ns.name):
            continue
        try:
            fetch.fetch(plugins_dir, name, collection.git, collection.ref)
            done.append(name)
        except fetch.FetchError as exc:
            failed.append({"collection": name, "kind": exc.kind, "message": str(exc)})

    def render(value: dict) -> str:
        lines = [f"fetched {name}" for name in value["fetched"]]
        return "\n".join(
            lines + [f"error: {f['collection']}: {f['message']}" for f in value["failed"]]
        )

    common.emit({"fetched": done, "failed": failed}, render, ns.json)
    if failed:
        raise SystemExit(1)


def _cmd_collection_auto_update(ns: argparse.Namespace) -> None:
    _not_a_worker()
    on = ns.state == "on"

    def change(raw: dict, config: PluginsConfig) -> None:
        if ns.name not in config.collections:
            raise _refuse(f"no collection {ns.name}")
        own = [
            i
            for i in config.plugins
            if i.endswith(f"@{ns.name}") and config.entry(i).auto_update is not None
        ]
        if on and own:
            raise _refuse(
                f"these plugins set their own auto_update: {', '.join(own)}; "
                "turn each off with `kraft admin plugin auto-update` first"
            )
        entry = raw["collections"][ns.name]
        entry.pop("auto_update", None)
        if on:
            entry["auto_update"] = True

    _edit(change)
    common.emit(
        {"collection": ns.name, "auto_update": on},
        lambda v: f"{ns.name}: auto-update {ns.state}",
        ns.json,
    )


def _cmd_collection_remove(ns: argparse.Namespace) -> None:
    _not_a_worker()
    config_dir, plugins_dir = _home()
    gone: list[str] = []

    def change(raw: dict, config: PluginsConfig) -> None:
        collection = config.collections.get(ns.name)
        if collection is None:
            raise _refuse(f"no collection {ns.name}")
        try:
            locked = PluginsLock.load(config_dir / PluginsLock.FILE).plugins
        except ConfigError as exc:
            raise _refuse(str(exc)) from exc
        installed = sorted(i for i in {*config.plugins, *locked} if i.endswith(f"@{ns.name}"))
        if installed:
            raise _refuse(
                f"collection {ns.name} still has plugins installed: {', '.join(installed)}; "
                "uninstall them first"
            )
        del raw["collections"][ns.name]
        if collection.git is not None:
            gone.append(collection.git)

    _edit(change)
    for url in gone:
        shutil.rmtree(fetch.mirror_path(plugins_dir, ns.name, url), ignore_errors=True)
    common.emit({"removed": ns.name}, lambda v: f"removed collection {ns.name}", ns.json)


def _result_view(result: plugin_update.Result) -> dict:
    reviewed = result.review
    return {
        "plugin": result.plugin_id,
        "outcome": result.outcome,
        "kind": result.kind,
        "problems": [review.escape(fetch.redact(p)) for p in result.problems],
        "note": result.note,
        "review": None
        if reviewed is None
        else {
            "old_version": reviewed.old_version,
            "new_version": reviewed.new_version,
            "reach": [review.escape(line) for line in reviewed.reach],
            "content": [review.escape(line) for line in reviewed.content],
        },
    }


def _render_results(views: list[dict]) -> str:
    lines = []
    for view in views:
        said = "; ".join(view["problems"]) or view["note"] or ""
        lines.append(f"{view['plugin']}: {view['outcome']}{': ' + said if said else ''}")
    return "\n".join(lines) or "no plugins installed"


def _run_update(
    ns: argparse.Namespace, ids: list[str] | None, *, check: bool = False, **kw
) -> list[plugin_update.Result]:
    """`update` for `ids`, each review printed (to stderr under `--json`) and
    answered: no under `--check`, yes under `-y`, else asked at the terminal."""
    declined_blind = []

    def accept(reviewed: review.Review) -> bool:
        print(review.render(reviewed), file=sys.stderr if ns.json else sys.stdout)
        if check:
            return False
        if ns.yes:
            return True
        if not sys.stdin.isatty():
            declined_blind.append(reviewed.plugin_id)
            return False
        return input(f"Apply {reviewed.plugin_id}? [y/N] ").strip().lower() in ("y", "yes")

    config_dir, plugins_dir = _home()
    try:
        results = plugin_update.update(
            ids, accept=accept, config_dir=config_dir, plugins_root=plugins_dir, **kw
        )
    except plugin_update.Refused as exc:
        raise _refuse("; ".join(exc.problems)) from exc
    except (ConfigError, PolicyError) as exc:
        raise _refuse(str(exc)) from exc
    if declined_blind:
        print(
            f"declined {', '.join(declined_blind)}: no terminal to ask at; pass -y to accept",
            file=sys.stderr,
        )
    if any(r.outcome == "applied" for r in results):
        _reload()
    return results


def _cmd_install(ns: argparse.Namespace) -> None:
    _not_a_worker()
    config_dir, _plugins_dir = _home()
    try:
        locked = PluginsLock.load(config_dir / PluginsLock.FILE).plugins
        config = PluginsConfig.load(config_dir / PluginsConfig.FILE)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    if ns.plugin in locked and not ns.re_install:
        raise _refuse(
            f"{ns.plugin} is already installed; `update` it, or pass --re-install to take the "
            "newest commit"
        )
    entries = None
    if ns.plugin not in config.plugins or ns.alias or ns.auto_update:
        was = config.entry(ns.plugin) if ns.plugin in config.plugins else PluginEntry()
        entry = PluginEntry(
            **{
                "as": ns.alias or was.as_,
                "enabled": was.enabled,
                "auto_update": True if ns.auto_update else was.auto_update,
            }
        )
        entries = {ns.plugin: entry}
    results = _run_update(ns, [ns.plugin], re_install=ns.re_install, entries=entries)
    common.emit([_result_view(r) for r in results], _render_results, ns.json)
    if results[0].outcome != "applied":
        raise SystemExit(1)


#: `update --check`'s exit code, worst first: could not check, would be
#: refused, an update is waiting.
_CHECK_CODES = (("failed", 3), ("refused", 2), ("declined", 1))


def _cmd_update(ns: argparse.Namespace) -> None:
    if not ns.check:
        _not_a_worker()
    results = _run_update(ns, ns.plugins or None, check=ns.check)
    views = [_result_view(r) for r in results]
    # Nobody was asked under `--check`: "declined" is an update that is waiting.
    waiting = {"declined": "update waiting"} if ns.check else {}
    common.emit(
        views,
        lambda v: _render_results(
            [{**x, "outcome": waiting.get(x["outcome"], x["outcome"])} for x in v]
        ),
        ns.json,
    )
    outcomes = {r.outcome for r in results}
    if ns.check:
        raise SystemExit(next((code for outcome, code in _CHECK_CODES if outcome in outcomes), 0))
    if outcomes & {"failed", "refused", "declined"}:
        raise SystemExit(1)


def _cmd_auto_update(ns: argparse.Namespace) -> None:
    _not_a_worker()
    on = ns.state == "on"

    def change(raw: dict, config: PluginsConfig) -> None:
        _installed_id(config, ns.plugin)
        entry = _long_entry(raw, ns.plugin)
        entry.pop("auto_update", None)
        if on:
            entry["auto_update"] = True
        _short_entry(raw, ns.plugin)

    _edit(change)
    common.emit(
        {"plugin": ns.plugin, "auto_update": on},
        lambda v: f"{ns.plugin}: auto-update {ns.state}",
        ns.json,
    )


def _still_referenced(config: PluginsConfig, config_dir: Path, plugin_id: str, verb: str) -> None:
    """Refuse while the instance's own files name the plugin's namespace: with
    the plugin gone each would stop resolving, or be handed to the agent as
    another tool's plugin skill."""
    try:
        locked = PluginsLock.load(config_dir / PluginsLock.FILE).plugins.get(plugin_id)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    namespace = locked.namespace if locked else config.namespace(plugin_id)
    uses = [
        f"{where}: {key} {value!r}"
        for where, key, _kind, value in plugin_update.references(config_dir)
        if value.split(":", 1)[0] == namespace
    ]
    if uses:
        raise _refuse(
            f"cannot {verb} {plugin_id}: its namespace {namespace!r} is still referenced by\n  "
            + "\n  ".join(uses)
        )


def _cmd_enable(ns: argparse.Namespace) -> None:
    _not_a_worker()
    on = ns.plugin_verb == "enable"
    config_dir, _plugins_dir = _home()

    def change(raw: dict, config: PluginsConfig) -> None:
        _installed_id(config, ns.plugin)
        if not on:
            _still_referenced(config, config_dir, ns.plugin, "disable")
        _long_entry(raw, ns.plugin)["enabled"] = on
        _short_entry(raw, ns.plugin)

    _edit(change)
    _reload()
    print(f"{ns.plugin}: {'enabled' if on else 'disabled'}")


def _cmd_uninstall(ns: argparse.Namespace) -> None:
    _not_a_worker()
    config_dir, _plugins_dir = _home()
    lock_file = config_dir / PluginsLock.FILE
    try:
        lock = read_yaml(lock_file, {})
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    locked = ns.plugin in (lock.get("plugins") or {})

    def change(raw: dict, config: PluginsConfig) -> None:
        if ns.plugin not in config.plugins and not locked:
            raise _refuse(f"{ns.plugin} is not installed")
        if ns.plugin in config.plugins:
            _still_referenced(config, config_dir, ns.plugin, "uninstall")
        raw["plugins"].pop(ns.plugin, None)

    def drop_lock_entry() -> None:
        # After plugins.yaml: a crash between the two leaves a known
        # "locked, not listed" entry.
        if locked:
            current = read_yaml(lock_file, {})
            current["plugins"].pop(ns.plugin, None)
            write_yaml(lock_file, current)

    _edit(change, drop_lock_entry)
    plugin_update.clear_status(_plugins_dir, {ns.plugin})
    _reload()
    common.emit({"uninstalled": ns.plugin}, lambda v: f"uninstalled {ns.plugin}", ns.json)


def _cmd_list(ns: argparse.Namespace) -> None:
    config_dir, plugins_dir = _home()
    try:
        config = PluginsConfig.load(config_dir / PluginsConfig.FILE)
        lock = PluginsLock.load(config_dir / PluginsLock.FILE)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    rows = []
    for plugin in load.installed(config_dir, plugins_dir):
        collection = config.collections.get(plugin.id.split("@", 1)[1])
        listed = plugin.id in config.plugins
        entry = config.entry(plugin.id) if listed else None
        locked = lock.plugins.get(plugin.id)
        pending = []
        if listed and locked and config.namespace(plugin.id) != locked.namespace:
            pending.append("alias change pending")
        if collection and locked and collection.ref != locked.ref:
            pending.append("ref change pending")
        if collection and locked and plugin_update._moved(collection, locked):
            pending.append("collection URL change pending")
        auto = None
        if collection is not None and collection.auto_update:
            auto = "collection"
        elif entry is not None and entry.auto_update:
            auto = "plugin"
        rows.append(
            {
                "id": plugin.id,
                "namespace": plugin.namespace,
                "version": plugin.version or None,
                "commit": locked.commit if locked else None,
                "enabled": bool(entry and entry.enabled),
                "auto_update": auto,
                "local_directory": bool(collection and collection.path),
                "pending": pending,
                "left_out": plugin.left_out,
            }
        )

    def render(value: list[dict]) -> str:
        lines = []
        for row in value:
            notes = [
                *(["local directory"] if row["local_directory"] else []),
                *([f"auto-update ({row['auto_update']})"] if row["auto_update"] else []),
                *row["pending"],
                *([row["left_out"]] if row["left_out"] else []),
            ]
            commit = (row["commit"] or "-")[:12]
            lines.append(
                f"{row['id']}  {row['namespace']}  {row['version'] or '-'}  {commit}"
                + (f"  ({'; '.join(notes)})" if notes else "")
            )
        return "\n".join(lines) or "no plugins installed"

    common.emit(rows, render, ns.json)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    plugin_p = subs.add_parser("plugin", help="Kraft plugins and collections")
    verbs = plugin_p.add_subparsers(dest="plugin_verb", required=True)

    def verb(to, name: str, func, help: str, *, json: bool = True) -> argparse.ArgumentParser:
        parser = to.add_parser(name, parents=[common_parser] if json else [], help=help)
        parser.set_defaults(func=func)
        return parser

    def plugin_id(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("plugin", metavar="PLUGIN@COLLECTION")

    validate_p = verb(
        verbs,
        "validate",
        _cmd_validate,
        "check a collection or a plugin directory; installs nothing, exit 1 on any problem",
    )
    validate_p.add_argument(
        "path", metavar="PATH", help="a collection or a single plugin directory"
    )

    collection_p = verbs.add_parser("collection", help="the collections this instance knows")
    collections = collection_p.add_subparsers(dest="collection_verb", required=True)
    add_p = verb(collections, "add", _cmd_collection_add, "add a collection; installs nothing")
    add_p.add_argument("source", metavar="SOURCE", help="owner/repo, a git URL, or a directory")
    add_p.add_argument("--ref", help="a branch, tag or commit; default: the default branch")
    add_p.add_argument(
        "--auto-update", action="store_true", help="every plugin installed from it updates itself"
    )
    verb(collections, "list", _cmd_collection_list, "collections and what each publishes")
    update_c = verb(
        collections,
        "update",
        _cmd_collection_update,
        "fetch the newest catalogue; installed plugins do not change",
    )
    update_c.add_argument("name", metavar="NAME", nargs="?")
    auto_c = verb(
        collections, "auto-update", _cmd_collection_auto_update, "set a collection's auto-update"
    )
    auto_c.add_argument("name", metavar="NAME")
    auto_c.add_argument("state", choices=("on", "off"))
    remove_c = verb(collections, "remove", _cmd_collection_remove, "forget a collection")
    remove_c.add_argument("name", metavar="NAME")

    install_p = verb(verbs, "install", _cmd_install, "install a plugin, after a review")
    plugin_id(install_p)
    install_p.add_argument("--as", dest="alias", metavar="ALIAS", help="its namespace")
    install_p.add_argument("--auto-update", action="store_true")
    install_p.add_argument(
        "--re-install",
        action="store_true",
        help="take the newest commit even at an unchanged version",
    )
    install_p.add_argument("-y", "--yes", action="store_true", help="accept the review")
    update_p = verb(verbs, "update", _cmd_update, "update installed plugins, each after a review")
    update_p.add_argument("plugins", metavar="PLUGIN@COLLECTION", nargs="*")
    answer = update_p.add_mutually_exclusive_group()
    answer.add_argument("-y", "--yes", action="store_true", help="accept every review")
    answer.add_argument(
        "--check",
        action="store_true",
        help="print the reviews and change nothing; exit 1 when an update is waiting, "
        "2 when one would be refused, 3 when it could not check",
    )
    auto_p = verb(verbs, "auto-update", _cmd_auto_update, "set one plugin's auto-update")
    plugin_id(auto_p)
    auto_p.add_argument("state", choices=("on", "off"))
    plugin_id(verb(verbs, "enable", _cmd_enable, "load an installed plugin", json=False))
    plugin_id(
        verb(verbs, "disable", _cmd_enable, "keep a plugin installed but not loaded", json=False)
    )
    plugin_id(verb(verbs, "uninstall", _cmd_uninstall, "remove an installed plugin"))
    verb(verbs, "list", _cmd_list, "installed plugins")
