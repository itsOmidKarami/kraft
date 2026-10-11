"""Fixtures for Kraft add-ons: a local template directory and an installed
add-on's store directory, as the library and the profile table read them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from kraft.addons import fetch, manifest
from kraft.addons.load import InstalledAddon
from kraft.config import read_yaml, write_yaml
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
) -> InstalledAddon:
    """One add-on's store directory at `tmp_path/store/<name>`, in the fixed
    layout, and the `InstalledAddon` that names it. `skills` is name to
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
    return InstalledAddon(
        id=f"{name}@acme", name=name, namespace=alias or name, version="1.0.0", root=root
    )


def addon_json(name: str, **over) -> dict:
    """A valid `.kraft/addon.json` for `name`, requiring the harness `AGENT` runs on."""
    requires = {"kraft": "2", "harnesses": ["codex"]}
    return {"name": name, "version": "1.0.0", "requires": requires, **over}


def _write_addon(repo: Path, addon: str, spec: dict) -> None:
    base = f"addons/{addon}"
    fields = {"version": spec["version"]} if "version" in spec else {}
    write(
        repo,
        f"{base}/.kraft/addon.json",
        json.dumps(addon_json(addon, **fields, **spec.get("manifest", {}))),
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


def make_collection(tmp_path: Path, addons: dict[str, dict], *, name: str = "acme") -> Path:
    """A committed git repository that is a collection named `name`, with one
    add-on per entry of `addons` at `addons/<addon>/`. Each spec may give
    `version`, `library`, `chains`, `skills`, `profiles` (as `installed` takes
    them), `manifest` (keys laid over `addon_json`) and `files` (relative path
    to raw text, for anything else). A directory collection is the same path."""
    repo = make_repo(tmp_path, f"{name}-kraft")
    for addon, spec in addons.items():
        _write_addon(repo, addon, spec)
    entries = [{"name": addon, "source": f"./addons/{addon}"} for addon in addons]
    write(
        repo,
        ".kraft/collection.json",
        json.dumps(
            {"name": name, "owner": {"name": "ACME", "email": "p@acme.dev"}, "addons": entries}
        ),
    )
    commit_all(repo, "collection")
    return repo


def publish(repo: Path, addon: str, **spec) -> str:
    """Commit a new state of `addon` to the collection at `repo`: the files
    `spec` names (as `make_collection` takes them) are rewritten, the rest
    kept. Returns the new commit."""
    _write_addon(repo, addon, spec)
    commit_all(repo, f"{addon} {spec.get('version', '')}")
    return git(repo, "rev-parse", "HEAD").strip()


def instance(
    tmp_path: Path, collections: dict[str, dict] | None = None, **local
) -> tuple[Path, Path]:
    """A Kraft home with no add-on installed: `(config directory, run/addons)`.
    `local` is what `home` takes; `collections` is `addons.yaml`'s section.
    Its `harnesses.yaml` defines the harness the fixture add-ons require."""
    config_dir = home(tmp_path, **local)
    write_yaml(config_dir / "harnesses.yaml", {"harnesses": {"codex": {"provider": "codex"}}})
    write_yaml(config_dir / "addons.yaml", {"collections": collections or {}, "addons": {}})
    return config_dir, tmp_path / "run" / "addons"


def install(
    config_dir: Path,
    addons_dir: Path,
    collection: Path,
    addon: str,
    *,
    alias: str | None = None,
    ref: str | None = None,
) -> Path:
    """What `kraft admin addon install` leaves behind, without its review:
    the add-on extracted from `collection` into `addons_dir/store/`, an entry
    in `config_dir/addons.yaml` and one in `addons.lock`. Returns the store
    directory."""
    url = collection.as_uri()
    mirror, commit = fetch.fetch(addons_dir, "fixture", url, ref)
    where = manifest.COLLECTION_JSON
    text = fetch.read_file(mirror, commit, where, fetch.MAX_JSON).decode()
    listed = manifest.collection(manifest.parse(text, where), where)
    source = next(e.source for e in listed.addons if e.name == addon)
    extracted = fetch.extract_git(mirror, commit, source)
    store = fetch.write_store(addons_dir, extracted)
    addon_id = f"{addon}@{listed.name}"
    config = read_yaml(config_dir / "addons.yaml", {"collections": {}, "addons": {}})
    config["collections"][listed.name] = {"git": url, **({"ref": ref} if ref else {})}
    config["addons"][addon_id] = {"as": alias} if alias else True
    lock = read_yaml(config_dir / "addons.lock", {"lock_version": 1, "addons": {}})
    lock["addons"][addon_id] = {
        "namespace": alias or addon,
        "ref": ref,
        "git": url,
        "commit": commit,
        "source": source,
        "tree": extracted.tree,
        "digest": fetch.digest(extracted.files),
        "version": json.loads(extracted.files[manifest.ADDON_JSON][1])["version"],
        "updated_at": "2026-10-08T12:00:00Z",
    }
    write_yaml(config_dir / "addons.yaml", config)
    write_yaml(config_dir / "addons.lock", lock)
    # The instance defines the harness the fixture add-ons require.
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
    """An add-on named `release` as extraction hands it on, built in memory:
    what `update.check` and `review.review` take."""
    declared = addon_json("release", **(manifest_fields or {}))
    files = {manifest.ADDON_JSON: ("100644", json.dumps(declared).encode())}
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
    """Delete an extracted add-on: a cleared run directory, or a store GC took."""
    for path in [store, *store.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
    shutil.rmtree(store)


def edit_addons_yaml(config_dir: Path, change) -> None:
    """A hand edit: `change(parsed addons.yaml)`, written back."""
    written = read_yaml(config_dir / "addons.yaml")
    change(written)
    write_yaml(config_dir / "addons.yaml", written)


def load_release(client, tmp_path: Path, **spec) -> Path:
    """`release@acme` installed into a running test server and loaded: a task
    `base` that names its own skill, and a chain `ship`. Returns its store."""
    from kraft.api import deps

    st = client.app.state
    addon = {
        "library": {"tasks": {"base": {**AGENT, "skill": "notes"}}},
        "chains": {"ship": chain("base")},
        "skills": {"notes": "the method"},
        **spec,
    }
    collection = make_collection(tmp_path, {"release": addon})
    store = install(st.templates_dir, st.run_dirs.addons, collection, "release")
    deps._reload_templates(st)
    return store


def install_release(
    tmp_path: Path, version: str = "1.0.0", *, fallback: bool = True, provider: str = "fake"
) -> tuple[dict, Path]:
    """`release@acme` at `version` installed into this process's own Kraft
    home (`paths.config_dir()`, `load.addons_dir()`): a skill `notes` that
    says `the <version> method`, and agent profiles `deep` (with a `fallback:`
    to `cheap` unless `fallback` is False) and `cheap` on `provider`. A second
    call publishes the next version to the same collection and moves the lock
    onto it. Returns what a work item's snapshot pins for it (`ResolvedChain.
    add-ons`) and the version's store."""
    from kraft import paths
    from kraft.addons import load

    deep = {
        "model": {provider: "opus"},
        **({"fallback": [{"profile": "release:cheap"}]} if fallback else {}),
    }
    spec = {
        "version": version,
        "skills": {"notes": f"the {version} method"},
        "profiles": {"deep": deep, "cheap": {"model": {provider: "haiku"}}},
    }
    collection = tmp_path / "acme-kraft"
    if collection.exists():
        publish(collection, "release", **spec)
    else:
        make_collection(tmp_path, {"release": spec})
    config_dir, addons_dir = paths.config_dir(), load.addons_dir()
    store = install(config_dir, addons_dir, collection, "release")
    (found,) = [p for p in load.installed(config_dir, addons_dir) if p.id == "release@acme"]
    return {"release": found.pin}, store


def pinned_chain(nodes, pins: dict, *, repo):
    """`nodes` (authored V1 node mappings) materialized as a snapshot that
    pins `pins`, the way an item filed while those add-on versions were the
    lock's records it."""
    from dataclasses import replace

    from support.harness import v1_chain, v1_resolved

    return v1_chain(replace(v1_resolved(nodes), addons=pins), repo=repo)
