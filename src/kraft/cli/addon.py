"""`kraft admin addon`: Kraft add-ons and collections.

The verbs edit `addons.yaml` and run `kraft.addons.update.update`; each one
that changes what loads asks a running server to rebuild its library from the
new lock. A worker runs none of the ones that write.

`validate` runs every check an install would: extraction under the limits,
the manifests, the Kraft version, and a lint, against this instance when
there is a Kraft home and of each add-on on its own when there is none. A
child add-on is judged with its ancestors, found in the path validated, each
`--with` directory, or the instance. It installs nothing and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from kraft import client, harness, update
from kraft.addons import fetch, flow, inherit, load, manifest, review
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


def _real_paths(text: str, paths: Mapping[Path, Path | str]) -> str:
    """`text` with each scratch store in `paths` written as the directory the
    add-on was read from, so no message names a copy that is deleted by the time
    it is read."""
    for store, real in paths.items():
        text = text.replace(str(store), str(real))
    return text


def _origins(
    ancestors: Sequence[InstalledAddon], origins: Mapping[Path, Path | str] | None
) -> dict[Path, Path | str]:
    """Where each of `ancestors` was read from: its own store when it is
    installed, else the directory `_ancestors_for` recorded in `origins`."""
    return {a.root: (origins or {}).get(a.root, a.root) for a in ancestors}


def _own_problems(
    issues: Sequence, ancestors: Sequence[InstalledAddon], paths: Mapping[Path, Path | str]
) -> list[tuple[Path | str | None, str]]:
    """`(ancestor's real directory or None, message)` for each issue: None for
    one in the add-on's own files. An ancestor's own chain failing also fails
    the copy of it the child serves under its namespace; that copy is dropped
    when the ancestor's own chain says it, so the failure is named once, as
    the ancestor's."""
    out = []
    for issue in issues:
        owner = next((a for a in ancestors if Path(issue.file).is_relative_to(a.root)), None)
        if owner is not None and issue.chain is not None:
            if issue.chain.partition(":")[0] != owner.namespace and any(
                other.file == issue.file and other.chain.partition(":")[0] == owner.namespace
                for other in issues
                if other.chain is not None
            ):
                continue
        out.append((paths[owner.root] if owner else None, _real_paths(issue.message, paths)))
    return out


def _lint(
    found: manifest.AddonManifest,
    extracted: fetch.Extracted,
    root: Path,
    ancestors: Sequence[InstalledAddon] = (),
    *,
    addon_id: str | None = None,
    origins: Mapping[Path, Path | str] | None = None,
) -> list[dict]:
    """Every chain of the add-on that does not resolve, with the add-on and its
    `ancestors` (parent first) as the only layers of an otherwise empty library.
    `addon_id` is its id in the collection validated, `<name>@validate` when
    there is none. A failure in an ancestor's own files is named by the
    directory that ancestor was read from (`origins`), not the add-on's."""
    with tempfile.TemporaryDirectory() as tmp:
        store = fetch.write_scratch(Path(tmp), extracted)
        addon_id = addon_id or f"{found.name}@validate"
        addons = [*ancestors, addon_update.as_installed(addon_id, found.name, found, store)]
        paths = {**_origins(ancestors, origins), store: root}
        try:
            library = TemplateLibrary.from_mappings(
                {}, (), library_path=store / "local-library.yaml", addons=addons
            )
            found_issues = _own_problems(library.lint(), ancestors, paths)
            HarnessProfileTable.from_mapping(
                {}, store / "harnesses.yaml", harnesses=harness.load(None).valid, addons=addons
            )
        except (TemplateLibraryError, TemplateEnvironmentError) as exc:
            found_issues = [(None, _real_paths(str(exc), paths))]
        # The author's own path, not the temporary copy's.
        return [{"file": str(where or root), "message": m} for where, m in found_issues]


def _lint_in(
    found: manifest.AddonManifest,
    extracted: fetch.Extracted,
    root: Path,
    collection: str | None,
    home: tuple[Path, Path],
    ancestors: Sequence[InstalledAddon] = (),
    origins: Mapping[Path, Path | str] | None = None,
) -> tuple[list[dict], list[str]]:
    """`(problems, warnings)` for installing the add-on into the instance at
    `home`. Problems are what it would be refused for: an unmet `requires`, a
    namespace another add-on holds, and anything of the instance's own that
    would stop resolving. A repo library's chain that would stop resolving is
    a warning, as an update holds rather than refuses for it
    (`a-broken-repo-library-chain-holds-an-update`). The same add-on already
    installed is replaced, not a clash, and so is each of its `ancestors`
    (parent first) found outside the instance."""
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
        candidate = addon_update.as_installed(
            f"{found.name}@{collection or 'validate'}", found.name, found, store
        )
        taken = {a.id for a in ancestors}
        tree = [*(p for p in loaded if p.id not in taken), *ancestors, candidate]
        libraries = {p.namespace for p in loaded if p.repo_id is not None}
        held_lines: list[str] = []
        try:
            before = addon_update.state_of(config_dir, now)
            after = addon_update.state_of(config_dir, tree)
            broken = addon_update.breaks(before, after, config_dir, found.name, exempt=libraries)
            held_lines = addon_update.held_breaks(before, after, libraries)
        except PolicyError as exc:
            broken = [str(exc)]
        paths = {**_origins(ancestors, origins), store: root}
        return (
            [{"file": str(root), "message": _real_paths(m, paths)} for m in broken],
            [f"{root}: {_real_paths(m, paths)}" for m in held_lines],
        )


def _read_extracted(
    extracted: fetch.Extracted, where: Path | str, entry: str | None
) -> tuple[manifest.AddonManifest, dict]:
    """The manifest of `extracted`, an add-on's files read from `where`, and
    its JSON as written. `entry` is its name in a `collection.json`, None for
    a directory on its own."""
    if manifest.ADDON_JSON not in extracted.files:
        raise manifest.ManifestError(f"{where}: no {manifest.ADDON_JSON}")
    file = f"{where}/{manifest.ADDON_JSON}"
    raw = manifest.parse(extracted.files[manifest.ADDON_JSON][1].decode(), file)
    found = manifest.addon(raw, file)
    manifest.check_addon(found, entry, extracted.files)
    return found, raw


#: An ancestor read from files: its manifest, its files, and where they were read from.
_Read = tuple[manifest.AddonManifest, fetch.Extracted, Path | str]


def _read_addon(where: Path | str, extracted: fetch.Extracted, entry: str | None) -> _Read:
    """An ancestor's manifest, with its files and the place they were read from."""
    return _read_extracted(extracted, where, entry)[0], extracted, where


def _collection_at(path: Path) -> tuple[str, dict[str, Path]] | None:
    """The collection directory at `path`: its name and each add-on's directory
    by name. None when `path` holds no `collection.json`."""
    file = path / manifest.COLLECTION_JSON
    if not file.is_file():
        return None
    listed = manifest.collection(manifest.parse(file.read_text(), str(file)), str(file))
    return listed.name, {e.name: path / e.source.removeprefix("./") for e in listed.addons}


def _with_match(where: Path, addon_id: str) -> tuple[Path, str | None] | None:
    """The directory of `addon_id` in the `--with` directory `where`, and its
    name there (None for an add-on directory given on its own); None when
    `where` does not have it. One that cannot be read does not have it: a
    later `--with` still may."""
    name, _, collection = addon_id.partition("@")
    try:
        if (listed := _collection_at(where)) is not None:
            found = listed[1].get(name) if listed[0] == collection else None
            return (found, name) if found is not None else None
        file = where / manifest.ADDON_JSON
        declared = manifest.parse(file.read_text(), str(file))
    except (OSError, ValueError, manifest.ManifestError):
        return None
    return (where, None) if declared.get("name") == name else None


def _in_dirs(addon_id: str, own: Mapping[str, Path], with_dirs: Sequence[Path]) -> _Read | None:
    """`addon_id` from `own` (the validated collection's add-on directories,
    by id), else from the first of `with_dirs` that has it: a collection of
    that name listing it, or an add-on directory of that name."""
    name = addon_id.split("@", 1)[0]
    if addon_id in own:
        return _read_addon(own[addon_id], fetch.extract_dir(own[addon_id]), name)
    for where in with_dirs:
        if (match := _with_match(where, addon_id)) is not None:
            return _read_addon(match[0], fetch.extract_dir(match[0]), match[1])
    return None


def _on_instance(
    addon_id: str,
    home: tuple[Path, Path],
    scratch: Path,
    held: Sequence[InstalledAddon],
    catalogues: dict[str, tuple],
) -> InstalledAddon | _Read | None:
    """`addon_id` as installed on the instance at `home` (`held`), else the
    current version that the instance's collection of that name publishes,
    fetched into `scratch`. `catalogues` keeps each collection fetched for
    the rest of the run."""
    name, _, collection = addon_id.partition("@")
    if hit := next((a for a in held if a.id == addon_id and a.repo_id is None), None):
        return hit
    config_dir, _addons_dir = home
    source = AddonsConfig.load(config_dir / AddonsConfig.FILE).collections.get(collection)
    if source is None:
        return None
    if collection not in catalogues:
        # Into the scratch directory: validate writes nothing to the home.
        catalogues[collection] = addon_update.catalogue(collection, source, scratch)
    mirror, commit, listed = catalogues[collection]
    entry = next((e for e in listed.addons if e.name == name), None)
    if entry is None:
        return None
    if mirror is None:
        where = Path(source.path) / entry.source.removeprefix("./")
        return _read_addon(where, fetch.extract_dir(where), name)
    where = f"{fetch.redact(source.git)}/{entry.source.removeprefix('./')}"
    return _read_addon(where, fetch.extract_git(mirror, commit, entry.source), name)


def _own_id(found: manifest.AddonManifest, own: Mapping[str, Path]) -> str:
    """The id the add-on `found` describes has: from the collection validated,
    or `<name>@validate` for a directory on its own."""
    return next((i for i in own if i.split("@", 1)[0] == found.name), f"{found.name}@validate")


def _ancestors_for(
    found: manifest.AddonManifest,
    own: Mapping[str, Path],
    with_dirs: Sequence[Path],
    home: tuple[Path, Path] | None,
    scratch: Path,
    *,
    held: Sequence[InstalledAddon] | None = None,
    catalogues: dict[str, tuple] | None = None,
    origins: dict[Path, Path | str] | None = None,
) -> list[InstalledAddon]:
    """The ancestors (parent first) of the add-on `found` describes, each
    resolved from, in order: `own` (the add-on directories of the collection
    validated, by id), each of `with_dirs` (a collection or an add-on
    directory), the add-on installed on the instance at `home` (`held`, read
    when not given), then the current version in the instance's collection of
    that name (`validate-resolves-a-parent-from-path-or-with`). Each one found
    outside the instance is written to a store under `scratch`. Raises
    `addon_update.Refused` when one cannot be found or read, or the tree is a
    cycle or too deep. Whether each version is in range is the library's to
    say, as it is at load. `origins`, when given, gets each store written under
    `scratch` mapped to the place its files were read from."""
    if held is None:
        held = load.installed(*home) if home else ()
    catalogues = {} if catalogues is None else catalogues
    ancestors: list[InstalledAddon] = []
    seen = {_own_id(found, own)}
    parent_id = found.parent[0] if found.parent else None
    while parent_id is not None and parent_id not in seen and len(ancestors) < inherit.MAX_DEPTH:
        try:
            parent = _in_dirs(parent_id, own, with_dirs)
            if parent is None and home is not None:
                parent = _on_instance(parent_id, home, scratch, held, catalogues)
        except (
            OSError,
            ValueError,
            ConfigError,
            fetch.FetchError,
            fetch.AddonRefused,
            manifest.ManifestError,
        ) as exc:
            raise addon_update.Refused(
                [f"its parent {parent_id} could not be read: {exc}"]
            ) from exc
        if parent is None:
            raise addon_update.Refused(
                [
                    f"its parent {parent_id} could not be found in PATH, --with or this "
                    "instance; pass its collection with --with"
                ]
            )
        if not isinstance(parent, InstalledAddon):
            read_manifest, read_files, read_from = parent
            store = fetch.write_scratch(scratch, read_files)
            parent = addon_update.as_installed(parent_id, read_manifest.name, read_manifest, store)
            if origins is not None:
                origins[store] = read_from
        ancestors.append(parent)
        seen.add(parent_id)
        parent_id = parent.parent
    if parent_id is not None:
        # The walk stopped at a loop or past the depth limit: said as load says it.
        candidate = addon_update.as_installed(_own_id(found, own), found.name, found, scratch)
        try:
            inherit.ancestry(candidate, ancestors)
        except inherit.InheritanceError as exc:
            raise addon_update.Refused([str(exc)]) from exc
    return ancestors


def validate(
    path: Path, home: tuple[Path, Path] | None = None, with_dirs: Sequence[Path] = ()
) -> dict:
    """`{addons, problems, warnings, skipped}` for the collection or the one
    add-on directory at `path`. With `home`, a Kraft home's config and add-ons
    directories, each add-on is also judged as an install into that instance.
    A child add-on is judged with its ancestors, found by `_ancestors_for` in
    `path`, `with_dirs` and the instance; one whose tree cannot be put
    together gets only the checks of its own files, and the problem that says
    why."""
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

    def holds_neither(where: Path) -> bool:
        if (where / manifest.COLLECTION_JSON).is_file() or (where / manifest.ADDON_JSON).is_file():
            return False
        problem(where, f"holds neither {manifest.COLLECTION_JSON} nor {manifest.ADDON_JSON}")
        return True

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
    elif holds_neither(path):
        return report
    else:
        targets = [(None, path)]
    with_dirs = [where for where in with_dirs if not holds_neither(where)]

    running = update.installed()
    if manifest.is_source_build(running):
        report["skipped"].append(f"requires.kraft: this Kraft ({running}) is a source build")
    own = {f"{entry}@{collection_name}": root for entry, root in targets if collection_name}
    held = load.installed(*home) if home else ()
    catalogues: dict[str, tuple] = {}
    origins: dict[Path, Path | str] = {}
    # Each ancestor found outside the instance is a store here until the end.
    with tempfile.TemporaryDirectory() as scratch:
        for entry, root in targets:
            addon_file = root / manifest.ADDON_JSON
            # Why its tree cannot be put together: said whatever else is wrong,
            # and in place of a lint that would say nothing true without it.
            no_tree: list[str] = []
            try:
                extracted = fetch.extract_dir(root)
                found, raw = _read_extracted(extracted, root, entry)
                try:
                    ancestors = _ancestors_for(
                        found,
                        own,
                        with_dirs,
                        home,
                        Path(scratch),
                        held=held,
                        catalogues=catalogues,
                        origins=origins,
                    )
                except addon_update.Refused as exc:
                    ancestors, no_tree = [], exc.problems
                addon_update.check(
                    found,
                    extracted.files,
                    other_namespaces={p.namespace for p in held} - {found.name},
                    named_by=addon_update.referrers(
                        found.name, [p for p in held if p.name != found.name]
                    ),
                    ancestors=ancestors,
                )
            except addon_update.Refused as exc:
                for why in [*no_tree, *exc.problems]:
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
            for why in no_tree:
                problem(root, why)
            why = manifest.kraft_compatible(found.requires.kraft, running)
            if why is not None:
                problem(addon_file, f"{found.name} {found.version} {why}; this is Kraft {running}")
                continue
            if not no_tree:
                addon_id = f"{entry}@{collection_name}" if collection_name else None
                problems, warnings = (
                    _lint_in(found, extracted, root, collection_name, home, ancestors, origins)
                    if home
                    else (
                        _lint(
                            found, extracted, root, ancestors, addon_id=addon_id, origins=origins
                        ),
                        [],
                    )
                )
                report["problems"] += problems
                report["warnings"] += warnings
            report["addons"].append(
                {
                    "name": found.name,
                    "version": found.version,
                    "digest": fetch.digest(extracted.files),
                    "parent": found.parent[0] if found.parent else None,
                }
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
    with_dirs = [Path(d).resolve() for d in ns.with_dirs or ()]
    report = validate(Path(ns.path).resolve(), home, with_dirs)
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

    def accept(group: review.Group) -> bool:
        print(review.render_group(group), file=sys.stderr if ns.json else sys.stdout)
        if check:
            return False
        if ns.yes:
            return True
        if not sys.stdin.isatty():
            declined_blind.append(group.title)
            return False
        return input(f"Apply {group.title}? [y/N] ").strip().lower() in ("y", "yes")

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
#: refused, an update is waiting or held by a range.
_CHECK_CODES = (("failed", 3), ("refused", 2), ("declined", 1), ("held", 1))


def _check_code(result: addon_update.Result) -> int:
    """`result`'s exit code under `_CHECK_CODES`, 0 for any other outcome. A
    relative held because its own new release breaks a chain would be refused,
    so it counts as `refused`."""
    outcome = "refused" if result.outcome == "held" and result.kind == "breaks" else result.outcome
    return next((code for name, code in _CHECK_CODES if name == outcome), 0)


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
    if ns.check:
        raise SystemExit(max((_check_code(r) for r in results), default=0))
    # A relative held by a chain break exits 2 here too: its own release would
    # be refused, even though what the update applied went in.
    if any(r.outcome == "held" and r.kind == "breaks" for r in results):
        raise SystemExit(2)
    if {r.outcome for r in results} & {"failed", "refused", "declined", "held"}:
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
    said = []
    if uses:
        said.append(
            f"cannot {verb} {addon_id}: its namespace {namespace!r} is still referenced by\n  "
            + "\n  ".join(uses)
        )
    if built_on := addon_update.built_on_refusal(
        addon_id, verb, load.installed(config_dir, addons_dir)
    ):
        said.append(built_on)
    if said:
        raise _refuse("\n".join(said))


def _parent_loads(raw: dict, config_dir: Path, addons_dir: Path, addon_id: str) -> None:
    """Refuse to enable a child whose parent is not installed, not loaded, or
    outside its range, judged as the instance would load with the entry
    enabled (`a-child-is-not-enabled-without-its-parent`)."""
    try:
        config = AddonsConfig.model_validate(raw)
    except ValidationError:
        return  # `_edit` refuses it, saying why
    found = load.installed(config_dir, addons_dir, config=config)
    for addon in found:
        if addon.id != addon_id or addon.parent is None:
            continue
        try:
            inherit.ancestry(addon, found)
        except inherit.InheritanceError as exc:
            why = str(exc)
            if addon.parent not in {a.id for a in found if a.repo_id is None}:
                why += f"; install it first: kraft admin addon install {addon.parent}"
            raise _refuse(f"cannot enable {addon_id}: {why}") from exc


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
        if on:
            _parent_loads(raw, config_dir, addons_dir, ns.addon)

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
    validate_p.add_argument(
        "--with",
        action="append",
        dest="with_dirs",
        metavar="DIR",
        help="a collection or add-on directory to find a child's parent in; repeatable",
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

    install_p = verb(
        verbs,
        "install",
        _cmd_install,
        "install an add-on and its missing ancestors, after a review",
    )
    addon_id(install_p)
    install_p.add_argument("--as", dest="alias", metavar="ALIAS", help="its namespace")
    install_p.add_argument("--auto-update", action="store_true")
    install_p.add_argument(
        "--re-install",
        action="store_true",
        help="take the newest commit even at an unchanged version",
    )
    install_p.add_argument("-y", "--yes", action="store_true", help="accept the review")
    update_p = verb(
        verbs,
        "update",
        _cmd_update,
        "update installed add-ons, one review per tree; exit 1 when one is refused, declined, held "
        "or failed, 2 when a relative is held because it breaks a chain",
    )
    update_p.add_argument("addons", metavar="ADDON@COLLECTION", nargs="*")
    answer = update_p.add_mutually_exclusive_group()
    answer.add_argument("-y", "--yes", action="store_true", help="accept every review")
    answer.add_argument(
        "--check",
        action="store_true",
        help="print the reviews and change nothing; exit 1 when an update is waiting or held by "
        "a range, 2 when one would be refused, 3 when it could not check",
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
