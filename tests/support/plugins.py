"""Fixtures for Kraft plugins: a local template directory and an installed
plugin's store directory, as the library and the profile table read them."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

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
    """A valid `.kraft/plugin.json` for `name`."""
    return {"name": name, "version": "1.0.0", "requires": {"kraft": "2"}, **over}


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
