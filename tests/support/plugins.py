"""Fixtures for Kraft plugins: a local template directory and an installed
plugin's store directory, as the library and the profile table read them."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from kraft.config import read_yaml, write_yaml
from kraft.plugins import fetch, manifest
from kraft.plugins.load import InstalledPlugin
from support.harness import commit_all, make_repo, write

#: The smallest agent task the V1 schema accepts.
AGENT = {"kind": "agent", "harness": "codex", "prompt": "local base"}


def chain(extends: str) -> dict:
    """A one-node chain whose single task extends `extends`."""
    return {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": extends}]}]}


def home(tmp_path: Path, *, library: dict | None = None, chains: dict | None = None) -> Path:
    """A local template directory at `tmp_path/config`: `library.yaml` and one
    file per entry of `chains` (name to body)."""
    root = tmp_path / "config"
    write(root, "library.yaml", yaml.safe_dump(library or {"tasks": {"base": AGENT}}))
    (root / "chains").mkdir(exist_ok=True)
    for name, body in (chains or {}).items():
        write(root, f"chains/{name}.yaml", yaml.safe_dump(body))
    return root


def installed(
    tmp_path: Path,
    name: str,
    *,
    alias: str | None = None,
    library: dict | None = None,
    chains: dict | None = None,
    skills: dict | None = None,
    profiles: dict | None = None,
) -> InstalledPlugin:
    """One plugin's store directory at `tmp_path/store/<name>`, in the fixed
    layout, and the `InstalledPlugin` that names it. `skills` is name to
    `SKILL.md` text; `profiles` is the `profiles:` section of `profiles.yaml`."""
    root = tmp_path / "store" / name
    root.mkdir(parents=True)
    if library is not None:
        write(root, "library.yaml", yaml.safe_dump(library))
    for chain_name, body in (chains or {}).items():
        write(root, f"chains/{chain_name}.yaml", yaml.safe_dump(body))
    for skill_name, text in (skills or {}).items():
        write(root, f"skills/{skill_name}/SKILL.md", text)
    if profiles is not None:
        write(root, "profiles.yaml", yaml.safe_dump({"profiles": profiles}))
    return InstalledPlugin(
        id=f"{name}@acme", name=name, namespace=alias or name, version="1.0.0", root=root
    )


def plugin_json(name: str, **over) -> dict:
    """A valid `.kraft/plugin.json` for `name`, requiring the harness `AGENT` runs on."""
    requires = {"kraft": "2", "harnesses": ["codex"]}
    return {"name": name, "version": "1.0.0", "requires": requires, **over}


def make_collection(tmp_path: Path, plugins: dict[str, dict], *, name: str = "acme") -> Path:
    """A committed git repository that is a collection named `name`, with one
    plugin per entry of `plugins` at `plugins/<plugin>/`. Each spec may give
    `library`, `chains`, `skills`, `profiles` (as `installed` takes them),
    `manifest` (keys laid over `plugin_json`) and `files` (relative path to raw
    text, for anything else). A directory collection is the same path."""
    repo = make_repo(tmp_path, f"{name}-kraft")
    entries = []
    for plugin, spec in plugins.items():
        base = f"plugins/{plugin}"
        write(
            repo,
            f"{base}/.kraft/plugin.json",
            json.dumps(plugin_json(plugin, **spec.get("manifest", {}))),
        )
        if "library" in spec:
            write(repo, f"{base}/library.yaml", yaml.safe_dump(spec["library"]))
        for chain_name, body in spec.get("chains", {}).items():
            write(repo, f"{base}/chains/{chain_name}.yaml", yaml.safe_dump(body))
        for skill_name, text in spec.get("skills", {}).items():
            write(repo, f"{base}/skills/{skill_name}/SKILL.md", text)
        if "profiles" in spec:
            write(repo, f"{base}/profiles.yaml", yaml.safe_dump({"profiles": spec["profiles"]}))
        for rel, text in spec.get("files", {}).items():
            write(repo, f"{base}/{rel}", text)
        entries.append({"name": plugin, "source": f"./{base}"})
    write(
        repo,
        ".kraft/collection.json",
        json.dumps(
            {"name": name, "owner": {"name": "ACME", "email": "p@acme.dev"}, "plugins": entries}
        ),
    )
    commit_all(repo, "collection")
    return repo


def install(
    config_dir: Path,
    plugins_dir: Path,
    collection: Path,
    plugin: str,
    *,
    alias: str | None = None,
    ref: str | None = None,
) -> Path:
    """What `kraft admin plugin install` leaves behind, without its review:
    the plugin extracted from `collection` into `plugins_dir/store/`, an entry
    in `config_dir/plugins.yaml` and one in `plugins.lock`. Returns the store
    directory."""
    url = collection.as_uri()
    mirror, commit = fetch.fetch(plugins_dir, "fixture", url, ref)
    where = manifest.COLLECTION_JSON
    text = fetch.read_file(mirror, commit, where, fetch.MAX_JSON).decode()
    listed = manifest.collection(manifest.parse(text, where), where)
    source = next(e.source for e in listed.plugins if e.name == plugin)
    extracted = fetch.extract_git(mirror, commit, source)
    store = fetch.write_store(plugins_dir, extracted)
    plugin_id = f"{plugin}@{listed.name}"
    config = read_yaml(config_dir / "plugins.yaml", {"collections": {}, "plugins": {}})
    config["collections"][listed.name] = {"git": url, **({"ref": ref} if ref else {})}
    config["plugins"][plugin_id] = {"as": alias} if alias else True
    lock = read_yaml(config_dir / "plugins.lock", {"lock_version": 1, "plugins": {}})
    lock["plugins"][plugin_id] = {
        "namespace": alias or plugin,
        "ref": ref,
        "commit": commit,
        "source": source,
        "tree": extracted.tree,
        "digest": fetch.digest(extracted.files),
        "version": json.loads(extracted.files[manifest.PLUGIN_JSON][1])["version"],
        "updated_at": "2026-10-08T12:00:00Z",
    }
    write_yaml(config_dir / "plugins.yaml", config)
    write_yaml(config_dir / "plugins.lock", lock)
    # The instance defines the harness the fixture plugins require.
    harnesses = read_yaml(config_dir / "harnesses.yaml")
    if "codex" not in (harnesses.get("harnesses") or {}):
        harnesses["harnesses"] = {
            **(harnesses.get("harnesses") or {}),
            "codex": {"provider": "codex"},
        }
        write_yaml(config_dir / "harnesses.yaml", harnesses)
    return store


def extracted(
    *,
    library: dict | None = None,
    chains: dict | None = None,
    skills: dict | None = None,
    profiles: dict | None = None,
    manifest_fields: dict | None = None,
) -> fetch.Extracted:
    """A plugin named `release` as extraction hands it on, built in memory:
    what `update.check` and `review.review` take."""
    declared = plugin_json("release", **(manifest_fields or {}))
    files = {manifest.PLUGIN_JSON: ("100644", json.dumps(declared).encode())}
    if library is not None:
        files["library.yaml"] = ("100644", yaml.safe_dump(library).encode())
    for chain_name, body in (chains or {}).items():
        files[f"chains/{chain_name}.yaml"] = ("100644", yaml.safe_dump(body).encode())
    for skill_name, text in (skills or {}).items():
        files[f"skills/{skill_name}/SKILL.md"] = ("100644", text.encode())
    if profiles is not None:
        files["profiles.yaml"] = ("100644", yaml.safe_dump({"profiles": profiles}).encode())
    return fetch.Extracted(files)
