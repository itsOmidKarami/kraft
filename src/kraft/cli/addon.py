"""`kraft admin addon`: Kraft add-ons and collections.

The verbs edit `addons.yaml` and run `kraft.addons.update.update`; each one
that changes what loads asks a running server to rebuild its library from the
new lock. A worker runs none of the ones that write.

`validate` runs every check an install would: extraction under the limits,
the manifests, the Kraft version, and a lint, against this instance when
there is a Kraft home and of each add-on on its own when there is none. It
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from kraft import client, harness, update
from kraft.addons import fetch, flow, load, manifest, review
from kraft.addons import update as addon_update
from kraft.addons.config import AddonsConfig, AddonsLock
from kraft.addons.load import InstalledAddon
from kraft.cli import common
from kraft.config import ConfigError, first_error, read_yaml, write_yaml
from kraft.policy import PolicyError
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

#: What only an instance can check: run when a Kraft home exists.
_NEEDS_AN_INSTANCE = (
    "no Kraft home was used, so these were skipped: requires.harnesses and requires.profiles, "
    "namespace clashes, and lint against an instance's own library"
)


def _lint(found: manifest.AddonManifest, extracted: fetch.Extracted, root: Path) -> list[dict]:
    """Every chain of the add-on that does not resolve, with the add-on as the
    only layer of an otherwise empty library."""
    with tempfile.TemporaryDirectory() as tmp:
        store = fetch.write_scratch(Path(tmp), extracted)
        addon = InstalledAddon(
            id=f"{found.name}@validate",
            name=found.name,
            namespace=found.name,
            version=found.version,
            root=store,
        )
        try:
            library = TemplateLibrary.from_mappings(
                {}, (), library_path=store / "local-library.yaml", addons=[addon]
            )
            messages = [issue.message for issue in library.lint()]
            HarnessProfileTable.from_mapping(
                {}, store / "harnesses.yaml", harnesses=harness.load(None).valid, addons=[addon]
            )
        except (TemplateLibraryError, TemplateEnvironmentError) as exc:
            messages = [str(exc)]
        # The author's own path, not the temporary copy's.
        return [{"file": str(root), "message": m.replace(str(store), str(root))} for m in messages]


def _lint_in(
    found: manifest.AddonManifest,
    extracted: fetch.Extracted,
    root: Path,
    collection: str | None,
    home: tuple[Path, Path],
) -> tuple[list[dict], list[str]]:
    """`(problems, warnings)` for installing the add-on into the instance at
    `home`. Problems are what it would be refused for: an unmet `requires`, a
    namespace another add-on holds, and anything of the instance's own that
    would stop resolving. A repo library's chain that would stop resolving is
    a warning, as an update holds rather than refuses for it
    (`a-broken-repo-library-chain-holds-an-update`). The same add-on already
    installed is replaced, not a clash."""
    config_dir, addons_dir = home

    def same(addon_id: str) -> bool:
        name, _, where = addon_id.partition("@")
        return name == found.name and collection in (None, where)

    now = load.installed(config_dir, addons_dir)
    loaded = [p for p in now if not same(p.id)]
    held = next((p.id for p in loaded if p.namespace == found.name), None)
    if held is not None:
        return [{"file": str(root), "message": f"namespace {found.name!r} is taken by {held}"}], []
    if why := load.instance_problem(found.name, found.requires, config_dir):
        return [{"file": str(root), "message": why}], []
    with tempfile.TemporaryDirectory() as tmp:
        store = fetch.write_scratch(Path(tmp), extracted)
        candidate = InstalledAddon(
            f"{found.name}@{collection or 'validate'}", found.name, found.name, found.version, store
        )
        libraries = {p.namespace for p in loaded if p.repo_id is not None}
        held_lines: list[str] = []
        try:
            before = addon_update.state_of(config_dir, now)
            after = addon_update.state_of(config_dir, [*loaded, candidate])
            broken = addon_update.breaks(before, after, config_dir, found.name, exempt=libraries)
            held_lines = addon_update.held_breaks(before, after, libraries)
        except PolicyError as exc:
            broken = [str(exc)]
        return (
            [{"file": str(root), "message": m.replace(str(store), str(root))} for m in broken],
            [f"{root}: {m.replace(str(store), str(root))}" for m in held_lines],
        )


def validate(path: Path, home: tuple[Path, Path] | None = None) -> dict:
    """`{addons, problems, warnings, skipped}` for the collection or the one
    add-on directory at `path`. With `home`, a Kraft home's config and add-ons
    directories, each add-on is also judged as an install into that instance."""
    report: dict = {
        "addons": [],
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
        targets = [(entry.name, path / entry.source.removeprefix("./")) for entry in listed.addons]
        collection_name = listed.name
    elif (path / manifest.ADDON_JSON).is_file():
        targets = [(None, path)]
    else:
        problem(path, f"holds neither {manifest.COLLECTION_JSON} nor {manifest.ADDON_JSON}")
        return report

    running = update.installed()
    if manifest.is_source_build(running):
        report["skipped"].append(f"requires.kraft: this Kraft ({running}) is a source build")
    for entry, root in targets:
        addon_file = root / manifest.ADDON_JSON
        try:
            extracted = fetch.extract_dir(root)
            if manifest.ADDON_JSON not in extracted.files:
                raise manifest.ManifestError(f"{root}: no {manifest.ADDON_JSON}")
            raw = manifest.parse(extracted.files[manifest.ADDON_JSON][1].decode(), str(addon_file))
            found = manifest.addon(raw, str(addon_file))
            manifest.check_addon(found, entry, extracted.files)
            held = load.installed(*home) if home else ()
            addon_update.check(
                found,
                extracted.files,
                other_namespaces={p.namespace for p in held} - {found.name},
                named_by=addon_update.referrers(
                    found.name, [p for p in held if p.name != found.name]
                ),
            )
        except addon_update.Refused as exc:
            for why in exc.problems:
                problem(root, why)
            continue
        except (fetch.AddonRefused, manifest.ManifestError) as exc:
            problem(root, str(exc))
            continue
        report["warnings"] += [
            f"{addon_file}: '{key}' is not read by Kraft"
            for key in manifest.unknown_keys(raw, manifest.AddonManifest)
        ]
        report["warnings"] += [
            f"{root}/{rel} is not part of the add-on layout and is ignored"
            for rel in extracted.skipped
        ]
        why = manifest.kraft_compatible(found.requires.kraft, running)
        if why is not None:
            problem(addon_file, f"{found.name} {found.version} {why}; this is Kraft {running}")
            continue
        problems, held = (
            _lint_in(found, extracted, root, collection_name, home)
            if home
            else (_lint(found, extracted, root), [])
        )
        report["problems"] += problems
        report["warnings"] += held
        report["addons"].append(
            {"name": found.name, "version": found.version, "digest": fetch.digest(extracted.files)}
        )
    return report


def _render(report: dict) -> str:
    lines = [f"{p['name']} {p['version']}  {p['digest']}" for p in report["addons"]]
    lines += [f"error: {p['file']}: {p['message']}" for p in report["problems"]]
    lines += [f"warning: {w}" for w in report["warnings"]]
    lines += [f"skipped: {s}" for s in report["skipped"]]
    return "\n".join(lines)


def _cmd_validate(ns: argparse.Namespace) -> None:
    config_dir, addons_dir = flow.home()
    # A Kraft home is one that has a library; CI has none.
    home = (config_dir, addons_dir) if (config_dir / "library.yaml").is_file() else None
    report = validate(Path(ns.path).resolve(), home)
    common.emit(report, _render, ns.json)
    if report["problems"]:
        raise SystemExit(1)


#: GitHub's `owner/repo`, which `collection add` writes as a full URL.
_OWNER_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")


def _refuse(message: str) -> SystemExit:
    return SystemExit(f"error: {fetch.redact(message)}")


@contextmanager
def _as_cli() -> Iterator[None]:
    """A refusal of the shared add-on flow, said as every verb here says one:
    `error: ...`, exit 1."""
    try:
        yield
    except flow.Refusal as exc:
        raise _refuse(str(exc)) from exc


def _not_a_worker() -> None:
    """Like `forbid_self_action`, consistency and not a security boundary: a
    worker does not change the instance it runs on."""
    if os.environ.get("KRAFT_WORK_ITEM_ID"):
        raise _refuse("a worker does not change the instance's add-ons")


def _reload() -> None:
    """Ask a running server to load the new lock. With none running, the next
    start reads it."""
    try:
        answer, error = asyncio.run(client.reload_addons()), None
    except ValueError as exc:
        answer, error = None, exc
    for line in flow.reload_warnings(answer, error):
        print(line, file=sys.stderr)


def _edit(
    change: Callable[[dict, AddonsConfig], None], then: Callable[[], None] | None = None
) -> AddonsConfig:
    """`addons.yaml` rewritten by `change(raw, parsed)` under the update lock,
    refused when the result is not a valid file. `then` runs after the write,
    still under the lock."""
    config_dir, addons_dir = flow.home()
    path = config_dir / AddonsConfig.FILE
    try:
        with addon_update.write_lock(addons_dir):
            raw = read_yaml(path, {})
            for section in ("collections", "addons"):
                raw[section] = raw.get(section) or {}
            change(raw, AddonsConfig.load(path))
            try:
                config = AddonsConfig.model_validate(raw)
            except ValidationError as exc:
                raise _refuse(first_error(exc, AddonsConfig.FILE)) from exc
            write_yaml(path, raw)
            if then is not None:
                then()
            return config
    except (ConfigError, addon_update.Busy) as exc:
        raise _refuse(str(exc)) from exc


def _installed_id(config: AddonsConfig, addon_id: str) -> None:
    if addon_id not in config.addons:
        raise _refuse(f"{addon_id} is not installed")


def _long_entry(raw: dict, addon_id: str) -> dict:
    """The entry as a map, whichever form the file wrote it in."""
    entry = raw["addons"][addon_id]
    raw["addons"][addon_id] = entry = {"enabled": entry} if isinstance(entry, bool) else entry
    return entry


def _short_entry(raw: dict, addon_id: str) -> None:
    """`true`/`false` again when nothing else is set."""
    entry = raw["addons"][addon_id]
    if set(entry) <= {"enabled"}:
        raw["addons"][addon_id] = entry.get("enabled", True)
    elif entry.get("enabled") is True:
        del entry["enabled"]


def _cmd_collection_add(ns: argparse.Namespace) -> None:
    _not_a_worker()
    _config_dir, addons_dir = flow.home()
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
            mirror, commit = fetch.fetch(addons_dir, "adding", url, ns.ref)
            text = fetch.read_file(mirror, commit, where, fetch.MAX_JSON).decode()
        name = manifest.collection(manifest.parse(text, where), where).name
    except (
        OSError,
        ValueError,
        fetch.FetchError,
        fetch.AddonRefused,
        manifest.ManifestError,
    ) as exc:
        raise _refuse(str(exc)) from exc

    def change(raw: dict, config: AddonsConfig) -> None:
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
            final = fetch.mirror_path(addons_dir, name, source["git"])
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
    with _as_cli():
        rows = flow.collection_rows()

    def render(value: list[dict]) -> str:
        lines = []
        for row in value:
            at = f" @ {row['ref']}" if row["ref"] else ""
            lines.append(f"{row['name']}  {row['source']}{at}")
            if row["addons"] is None:
                lines.append("  not fetched yet; run `kraft admin addon collection update`")
            for addon in row["addons"] or ():
                lines.append(f"  {addon['name']}{'  (installed)' if addon['installed'] else ''}")
        return "\n".join(lines) or "no collections"

    common.emit(rows, render, ns.json)


def _cmd_collection_update(ns: argparse.Namespace) -> None:
    _not_a_worker()  # it writes the mirrors the daemon later reads
    config_dir, addons_dir = flow.home()
    try:
        config = AddonsConfig.load(config_dir / AddonsConfig.FILE)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    if ns.name and ns.name not in config.collections:
        raise _refuse(f"no collection {ns.name}")
    done, failed = [], []
    for name, collection in config.collections.items():
        if collection.git is None or (ns.name and name != ns.name):
            continue
        try:
            fetch.fetch(addons_dir, name, collection.git, collection.ref)
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

    def change(raw: dict, config: AddonsConfig) -> None:
        if ns.name not in config.collections:
            raise _refuse(f"no collection {ns.name}")
        own = [
            i
            for i in config.addons
            if i.endswith(f"@{ns.name}") and config.entry(i).auto_update is not None
        ]
        if on and own:
            raise _refuse(
                f"these add-ons set their own auto_update: {', '.join(own)}; "
                "turn each off with `kraft admin addon auto-update` first"
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
    config_dir, addons_dir = flow.home()
    gone: list[str] = []

    def change(raw: dict, config: AddonsConfig) -> None:
        collection = config.collections.get(ns.name)
        if collection is None:
            raise _refuse(f"no collection {ns.name}")
        try:
            locked = AddonsLock.load(config_dir / AddonsLock.FILE).addons
        except ConfigError as exc:
            raise _refuse(str(exc)) from exc
        installed = sorted(i for i in {*config.addons, *locked} if i.endswith(f"@{ns.name}"))
        if installed:
            raise _refuse(
                f"collection {ns.name} still has add-ons installed: {', '.join(installed)}; "
                "uninstall them first"
            )
        del raw["collections"][ns.name]
        if collection.git is not None:
            gone.append(collection.git)

    _edit(change)
    for url in gone:
        shutil.rmtree(fetch.mirror_path(addons_dir, ns.name, url), ignore_errors=True)
    common.emit({"removed": ns.name}, lambda v: f"removed collection {ns.name}", ns.json)


def _run_update(
    ns: argparse.Namespace, ids: list[str] | None, *, check: bool = False, **kw
) -> list[addon_update.Result]:
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
            declined_blind.append(reviewed.addon_id)
            return False
        return input(f"Apply {reviewed.addon_id}? [y/N] ").strip().lower() in ("y", "yes")

    with _as_cli():
        results = flow.update(ids, accept, **kw)
    if declined_blind:
        print(
            f"declined {', '.join(declined_blind)}: no terminal to ask at; pass -y to accept",
            file=sys.stderr,
        )
    if flow.applied(results):
        _reload()
    return results


def _cmd_install(ns: argparse.Namespace) -> None:
    _not_a_worker()
    with _as_cli():
        entries = flow.install_entries(
            ns.addon, alias=ns.alias, auto_update=ns.auto_update, re_install=ns.re_install
        )
    results = _run_update(ns, [ns.addon], re_install=ns.re_install, entries=entries)
    common.emit([flow.result_view(r) for r in results], flow.render_results, ns.json)
    if results[0].outcome != "applied":
        raise SystemExit(1)


#: `update --check`'s exit code, worst first: could not check, would be
#: refused, an update is waiting.
_CHECK_CODES = (("failed", 3), ("refused", 2), ("declined", 1))


def _cmd_update(ns: argparse.Namespace) -> None:
    if not ns.check:
        _not_a_worker()
    results = _run_update(ns, ns.addons or None, check=ns.check)
    views = [flow.result_view(r) for r in results]
    # Nobody was asked under `--check`: "declined" is an update that is waiting.
    waiting = {"declined": "update waiting"} if ns.check else {}
    common.emit(
        views,
        lambda v: flow.render_results(
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

    def change(raw: dict, config: AddonsConfig) -> None:
        _installed_id(config, ns.addon)
        entry = _long_entry(raw, ns.addon)
        entry.pop("auto_update", None)
        if on:
            entry["auto_update"] = True
        _short_entry(raw, ns.addon)

    _edit(change)
    common.emit(
        {"addon": ns.addon, "auto_update": on},
        lambda v: f"{ns.addon}: auto-update {ns.state}",
        ns.json,
    )


def _still_referenced(
    config: AddonsConfig, config_dir: Path, addons_dir: Path, addon_id: str, verb: str
) -> None:
    """Refuse while the instance's own files name the add-on's namespace: with
    the add-on gone each would stop resolving, or be handed to the agent as
    another tool's plugin skill. Refuse too while a connected repository's
    library lists the add-on under `addons:`: its chains build on it."""
    try:
        locked = AddonsLock.load(config_dir / AddonsLock.FILE).addons.get(addon_id)
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    namespace = locked.namespace if locked else config.namespace(addon_id)
    uses = [
        f"{where}: {key} {value!r}"
        for where, key, _kind, value in addon_update.references(config_dir)
        if value.split(":", 1)[0] == namespace
    ]
    builds_on = addon_update.repo_library_dependents(
        addon_id, load.installed(config_dir, addons_dir)
    )
    said = []
    if uses:
        said.append(
            f"cannot {verb} {addon_id}: its namespace {namespace!r} is still referenced by\n  "
            + "\n  ".join(uses)
        )
    if builds_on:
        said.append(
            f"cannot {verb} {addon_id}: a connected repository's repo library builds on it\n  "
            + "\n  ".join(builds_on)
        )
    if said:
        raise _refuse("\n".join(said))


def _cmd_enable(ns: argparse.Namespace) -> None:
    _not_a_worker()
    on = ns.addon_verb == "enable"
    config_dir, addons_dir = flow.home()

    def change(raw: dict, config: AddonsConfig) -> None:
        _installed_id(config, ns.addon)
        if not on:
            _still_referenced(config, config_dir, addons_dir, ns.addon, "disable")
        _long_entry(raw, ns.addon)["enabled"] = on
        _short_entry(raw, ns.addon)

    _edit(change)
    _reload()
    print(f"{ns.addon}: {'enabled' if on else 'disabled'}")


def _cmd_uninstall(ns: argparse.Namespace) -> None:
    _not_a_worker()
    config_dir, addons_dir = flow.home()
    lock_file = config_dir / AddonsLock.FILE
    try:
        lock = read_yaml(lock_file, {})
    except ConfigError as exc:
        raise _refuse(str(exc)) from exc
    locked = ns.addon in (lock.get("addons") or {})

    def change(raw: dict, config: AddonsConfig) -> None:
        if ns.addon not in config.addons and not locked:
            raise _refuse(f"{ns.addon} is not installed")
        if ns.addon in config.addons:
            _still_referenced(config, config_dir, addons_dir, ns.addon, "uninstall")
        raw["addons"].pop(ns.addon, None)

    def drop_lock_entry() -> None:
        # After addons.yaml: a crash between the two leaves a known
        # "locked, not listed" entry.
        if locked:
            current = read_yaml(lock_file, {})
            current["addons"].pop(ns.addon, None)
            write_yaml(lock_file, current)

    _edit(change, drop_lock_entry)
    addon_update.clear_status(addons_dir, {ns.addon})
    _reload()
    common.emit({"uninstalled": ns.addon}, lambda v: f"uninstalled {ns.addon}", ns.json)


def _cmd_list(ns: argparse.Namespace) -> None:
    with _as_cli():
        rows = flow.addon_rows()

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
        return "\n".join(lines) or "no add-ons installed"

    common.emit(rows, render, ns.json)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    addon_p = subs.add_parser("addon", help="Kraft add-ons and collections")
    verbs = addon_p.add_subparsers(dest="addon_verb", required=True)

    def verb(to, name: str, func, help: str, *, json: bool = True) -> argparse.ArgumentParser:
        parser = to.add_parser(name, parents=[common_parser] if json else [], help=help)
        parser.set_defaults(func=func)
        return parser

    def addon_id(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("addon", metavar="ADDON@COLLECTION")

    validate_p = verb(
        verbs,
        "validate",
        _cmd_validate,
        "check a collection or an add-on directory; installs nothing, exit 1 on any problem",
    )
    validate_p.add_argument(
        "path", metavar="PATH", help="a collection or a single add-on directory"
    )

    collection_p = verbs.add_parser("collection", help="the collections this instance knows")
    collections = collection_p.add_subparsers(dest="collection_verb", required=True)
    add_p = verb(collections, "add", _cmd_collection_add, "add a collection; installs nothing")
    add_p.add_argument("source", metavar="SOURCE", help="owner/repo, a git URL, or a directory")
    add_p.add_argument("--ref", help="a branch, tag or commit; default: the default branch")
    add_p.add_argument(
        "--auto-update", action="store_true", help="every add-on installed from it updates itself"
    )
    verb(collections, "list", _cmd_collection_list, "collections and what each publishes")
    update_c = verb(
        collections,
        "update",
        _cmd_collection_update,
        "fetch the newest catalogue; installed add-ons do not change",
    )
    update_c.add_argument("name", metavar="NAME", nargs="?")
    auto_c = verb(
        collections, "auto-update", _cmd_collection_auto_update, "set a collection's auto-update"
    )
    auto_c.add_argument("name", metavar="NAME")
    auto_c.add_argument("state", choices=("on", "off"))
    remove_c = verb(collections, "remove", _cmd_collection_remove, "forget a collection")
    remove_c.add_argument("name", metavar="NAME")

    install_p = verb(verbs, "install", _cmd_install, "install an add-on, after a review")
    addon_id(install_p)
    install_p.add_argument("--as", dest="alias", metavar="ALIAS", help="its namespace")
    install_p.add_argument("--auto-update", action="store_true")
    install_p.add_argument(
        "--re-install",
        action="store_true",
        help="take the newest commit even at an unchanged version",
    )
    install_p.add_argument("-y", "--yes", action="store_true", help="accept the review")
    update_p = verb(verbs, "update", _cmd_update, "update installed add-ons, each after a review")
    update_p.add_argument("addons", metavar="ADDON@COLLECTION", nargs="*")
    answer = update_p.add_mutually_exclusive_group()
    answer.add_argument("-y", "--yes", action="store_true", help="accept every review")
    answer.add_argument(
        "--check",
        action="store_true",
        help="print the reviews and change nothing; exit 1 when an update is waiting, "
        "2 when one would be refused, 3 when it could not check",
    )
    auto_p = verb(verbs, "auto-update", _cmd_auto_update, "set one add-on's auto-update")
    addon_id(auto_p)
    auto_p.add_argument("state", choices=("on", "off"))
    addon_id(verb(verbs, "enable", _cmd_enable, "load an installed add-on", json=False))
    addon_id(
        verb(verbs, "disable", _cmd_enable, "keep an add-on installed but not loaded", json=False)
    )
    addon_id(verb(verbs, "uninstall", _cmd_uninstall, "remove an installed add-on"))
    verb(verbs, "list", _cmd_list, "installed add-ons")
