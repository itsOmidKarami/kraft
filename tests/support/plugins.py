"""Fixtures for Kraft plugins: a local template directory and an installed
plugin's store directory, as the library and the profile table read them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from kraft.config import read_yaml, write_yaml
from kraft.plugins import fetch, manifest
from kraft.plugins.load import InstalledPlugin
from support.harness import commit_all, git, make_repo, write

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


def _write_plugin(repo: Path, plugin: str, spec: dict) -> None:
    base = f"plugins/{plugin}"
    fields = {"version": spec["version"]} if "version" in spec else {}
    write(
        repo,
        f"{base}/.kraft/plugin.json",
        json.dumps(plugin_json(plugin, **fields, **spec.get("manifest", {}))),
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


def make_collection(tmp_path: Path, plugins: dict[str, dict], *, name: str = "acme") -> Path:
    """A committed git repository that is a collection named `name`, with one
    plugin per entry of `plugins` at `plugins/<plugin>/`. Each spec may give
    `version`, `library`, `chains`, `skills`, `profiles` (as `installed` takes
    them), `manifest` (keys laid over `plugin_json`) and `files` (relative path
    to raw text, for anything else). A directory collection is the same path."""
    repo = make_repo(tmp_path, f"{name}-kraft")
    for plugin, spec in plugins.items():
        _write_plugin(repo, plugin, spec)
    entries = [{"name": plugin, "source": f"./plugins/{plugin}"} for plugin in plugins]
    write(
        repo,
        ".kraft/collection.json",
        json.dumps(
            {"name": name, "owner": {"name": "ACME", "email": "p@acme.dev"}, "plugins": entries}
        ),
    )
    commit_all(repo, "collection")
    return repo


def publish(repo: Path, plugin: str, **spec) -> str:
    """Commit a new state of `plugin` to the collection at `repo`: the files
    `spec` names (as `make_collection` takes them) are rewritten, the rest
    kept. Returns the new commit."""
    _write_plugin(repo, plugin, spec)
    commit_all(repo, f"{plugin} {spec.get('version', '')}")
    return git(repo, "rev-parse", "HEAD").strip()


def instance(
    tmp_path: Path, collections: dict[str, dict] | None = None, **local
) -> tuple[Path, Path]:
    """A Kraft home with no plugin installed: `(config directory, run/plugins)`.
    `local` is what `home` takes; `collections` is `plugins.yaml`'s section.
    Its `harnesses.yaml` defines the harness the fixture plugins require."""
    config_dir = home(tmp_path, **local)
    write_yaml(config_dir / "harnesses.yaml", {"harnesses": {"codex": {"provider": "codex"}}})
    write_yaml(config_dir / "plugins.yaml", {"collections": collections or {}, "plugins": {}})
    return config_dir, tmp_path / "run" / "plugins"


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
        "git": url,
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


def drop_store(store: Path) -> None:
    """Delete an extracted plugin: a cleared run directory, or a store GC took."""
    for path in [store, *store.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
    shutil.rmtree(store)


def edit_plugins_yaml(config_dir: Path, change) -> None:
    """A hand edit: `change(parsed plugins.yaml)`, written back."""
    written = read_yaml(config_dir / "plugins.yaml")
    change(written)
    write_yaml(config_dir / "plugins.yaml", written)


def load_release(client, tmp_path: Path, **spec) -> Path:
    """`release@acme` installed into a running test server and loaded: a task
    `base` that names its own skill, and a chain `ship`. Returns its store."""
    from kraft.api import deps

    st = client.app.state
    plugin = {
        "library": {"tasks": {"base": {**AGENT, "skill": "notes"}}},
        "chains": {"ship": chain("base")},
        "skills": {"notes": "the method"},
        **spec,
    }
    collection = make_collection(tmp_path, {"release": plugin})
    store = install(st.templates_dir, st.run_dirs.plugins, collection, "release")
    deps._reload_templates(st)
    return store
