"""`kraft admin plugin`: Kraft plugins and collections.

`validate` runs every check an install would that needs no instance:
extraction under the limits, the manifests, the Kraft version, and a lint of
each plugin on its own. It installs nothing and writes nothing.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from kraft import harness, update
from kraft.cli import common
from kraft.plugins import fetch, manifest
from kraft.plugins.load import InstalledPlugin
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

#: What only an instance can check. Slice 3 runs these when a Kraft home exists.
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


def validate(path: Path) -> dict:
    """`{plugins, problems, warnings, skipped}` for the collection or the one
    plugin directory at `path`."""
    report: dict = {"plugins": [], "problems": [], "warnings": [], "skipped": [_NEEDS_AN_INSTANCE]}

    def problem(file: Path, message: str) -> None:
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
        report["problems"] += _lint(found, extracted, root)
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
    report = validate(Path(ns.path).resolve())
    common.emit(report, _render, ns.json)
    if report["problems"]:
        raise SystemExit(1)


def add(subs, common_parser: argparse.ArgumentParser) -> None:
    plugin_p = subs.add_parser("plugin", help="Kraft plugins and collections")
    verbs = plugin_p.add_subparsers(dest="plugin_verb", required=True)
    validate_p = verbs.add_parser(
        "validate",
        parents=[common_parser],
        help="check a collection or a plugin directory; installs nothing, exit 1 on any problem",
    )
    validate_p.add_argument(
        "path", metavar="PATH", help="a collection or a single plugin directory"
    )
    validate_p.set_defaults(func=_cmd_validate)
