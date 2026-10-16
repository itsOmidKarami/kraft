"""The add-on flow both front doors share (spec §4.3): `kraft admin addon`
prints it, the console's Add-ons draws it. Nothing here prints, asks or
exits. A refusal is a `Refusal`, which the CLI says as `error: ...` with exit
1, and the console in a toast."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from kraft import paths
from kraft.addons import fetch, load, manifest, review
from kraft.addons import update as addon_update
from kraft.addons.config import AddonEntry, AddonsConfig, AddonsLock
from kraft.config import ConfigError
from kraft.policy import PolicyError


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


def install_entries(
    addon_id: str,
    *,
    alias: str | None = None,
    auto_update: bool = False,
    re_install: bool = False,
) -> dict[str, AddonEntry] | None:
    """The `addons.yaml` entry an install writes once accepted, or None when
    the one already there stands. Refused for an add-on already installed,
    unless `re_install`."""
    config_dir, _addons_dir = home()
    try:
        locked = AddonsLock.load(config_dir / AddonsLock.FILE).addons
        config = AddonsConfig.load(config_dir / AddonsConfig.FILE)
    except ConfigError as exc:
        raise refusal(str(exc)) from exc
    if addon_id in locked and not re_install:
        raise refusal(
            f"{addon_id} is already installed; `update` it, or pass --re-install to take the "
            "newest commit"
        )
    if addon_id in config.addons and not alias and not auto_update:
        return None
    was = config.entry(addon_id) if addon_id in config.addons else AddonEntry()
    return {
        addon_id: AddonEntry(
            **{
                "as": alias or was.as_,
                "enabled": was.enabled,
                "auto_update": True if auto_update else was.auto_update,
            }
        )
    }


def update(
    ids: Sequence[str] | None,
    accept: Callable[[review.Review], bool],
    *,
    re_install: bool = False,
    entries: dict | None = None,
) -> list[addon_update.Result]:
    """`addon_update.update` on this instance, `accept` asked once for each
    review. Blocking: it reads git and the store, and waits on `accept`."""
    config_dir, addons_dir = home()
    try:
        return addon_update.update(
            ids,
            accept=accept,
            re_install=re_install,
            entries=entries,
            config_dir=config_dir,
            addons_root=addons_dir,
        )
    except addon_update.Refused as exc:
        raise refusal("; ".join(exc.problems)) from exc
    except (ConfigError, PolicyError) as exc:
        raise refusal(str(exc)) from exc


def applied(results: Sequence[addon_update.Result]) -> bool:
    """Whether any add-on was taken: then the running server reloads."""
    return any(r.outcome == "applied" for r in results)


def reload_warnings(answer: dict | None, error: Exception | None = None) -> list[str]:
    """What asking a running server to load the new lock leaves to say: why
    it did not, unless no server runs (the next start reads it); else each
    add-on a reload that answered still left out."""
    if error is not None:
        if "no Kraft server" in str(error):
            return []
        return [f"warning: the running server did not reload: {error}"]
    return [
        f"warning: the running server did not load {what}: {why}"
        for what, why in ((answer or {}).get("invalid_templates") or {}).items()
        if what == "addons.yaml" or what.startswith("add-on ")
    ]


def result_view(result: addon_update.Result) -> dict:
    reviewed = result.review
    return {
        "addon": result.addon_id,
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


def render_results(views: list[dict]) -> str:
    lines = []
    for view in views:
        said = "; ".join(view["problems"]) or view["note"] or ""
        lines.append(f"{view['addon']}: {view['outcome']}{': ' + said if said else ''}")
    return "\n".join(lines) or "no add-ons installed"
