"""The add-on flow both front doors share (spec §4.3): `kraft admin addon`
prints it, the console's Add-ons draws it. Nothing here prints, asks or
exits. A refusal is a `Refusal`, which the CLI says as `error: ...` with exit
1, and the console in a toast."""

from __future__ import annotations

from pathlib import Path

from kraft import paths
from kraft.addons import fetch, load, manifest
from kraft.addons import update as addon_update
from kraft.addons.config import AddonsConfig, AddonsLock
from kraft.config import ConfigError


class Refusal(Exception):
    """What the flow refused, in words with every URL's userinfo removed."""


def refusal(message: str) -> Refusal:
    return Refusal(fetch.redact(message))


def home() -> tuple[Path, Path]:
    """This instance's config directory and its `run/addons`."""
    return paths.config_dir(), load.addons_dir()


def _catalogue(name: str, collection, addons_dir: Path) -> list[str] | None:
    """The add-ons the collection listed when it was last fetched; None when
    it has not been. No network."""
    where = manifest.COLLECTION_JSON
    try:
        if collection.git is None:
            text = (Path(collection.path) / where).read_text()
        else:
            mirror = fetch.mirror_path(addons_dir, name, collection.git)
            if not mirror.is_dir():
                return None
            commit = fetch._git("rev-parse", "--verify", "FETCH_HEAD^{commit}", git_dir=mirror)
            text = fetch.read_file(mirror, commit.decode().strip(), where, fetch.MAX_JSON).decode()
        return [
            entry.name for entry in manifest.collection(manifest.parse(text, where), where).addons
        ]
    except (OSError, ValueError, fetch.FetchError, fetch.AddonRefused, manifest.ManifestError):
        return None


def collection_rows() -> list[dict]:
    """`kraft admin addon collection list`'s rows: each collection, where it
    comes from, and what it published when last fetched (None: never)."""
    config_dir, addons_dir = home()
    try:
        config = AddonsConfig.load(config_dir / AddonsConfig.FILE)
    except ConfigError as exc:
        raise refusal(str(exc)) from exc
    rows = []
    for name, collection in config.collections.items():
        listed = _catalogue(name, collection, addons_dir)
        rows.append(
            {
                "name": name,
                "source": fetch.redact(collection.git) if collection.git else collection.path,
                "ref": collection.ref,
                "auto_update": collection.auto_update,
                "addons": None
                if listed is None
                else [{"name": p, "installed": f"{p}@{name}" in config.addons} for p in listed],
            }
        )
    return rows


def addon_rows() -> list[dict]:
    """`kraft admin addon list`'s rows: every installed add-on but a repo
    library's, with what it loads as and anything pending."""
    config_dir, addons_dir = home()
    try:
        config = AddonsConfig.load(config_dir / AddonsConfig.FILE)
        lock = AddonsLock.load(config_dir / AddonsLock.FILE)
    except ConfigError as exc:
        raise refusal(str(exc)) from exc
    rows = []
    for addon in load.installed(config_dir, addons_dir):
        if addon.repo_id is not None:
            continue  # `kraft repo library` is how a repo library is listed and taken
        collection = config.collections.get(addon.id.split("@", 1)[1])
        listed = addon.id in config.addons
        entry = config.entry(addon.id) if listed else None
        locked = lock.addons.get(addon.id)
        pending = []
        if listed and locked and config.namespace(addon.id) != locked.namespace:
            pending.append("alias change pending")
        if collection and locked and collection.ref != locked.ref:
            pending.append("ref change pending")
        if collection and locked and addon_update._moved(collection, locked):
            pending.append("collection URL change pending")
        auto = None
        if collection is not None and collection.auto_update:
            auto = "collection"
        elif entry is not None and entry.auto_update:
            auto = "addon"
        rows.append(
            {
                "id": addon.id,
                "namespace": addon.namespace,
                "version": addon.version or None,
                "commit": locked.commit if locked else None,
                "enabled": bool(entry and entry.enabled),
                "auto_update": auto,
                "local_directory": bool(collection and collection.path),
                "pending": pending,
                "left_out": addon.left_out,
            }
        )
    return rows
