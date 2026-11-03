"""The built-in `kraft-default@kraft` add-on: its identity and where its files
come from. Kraft's own chains and library are read from the installed package,
not from a home."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

import kraft
from kraft import paths, update
from kraft.addons import fetch, manifest

ID = "kraft-default@kraft"
NAMESPACE = "kraft-default"
COLLECTION = "kraft"


def source_dir() -> Path:
    """Where the built-in's files are: the checkout's `config/` for a source
    build (even beside a stale bundle), else the wheel's bundle, else a
    release-tag checkout's `config/`."""
    checkout = Path(kraft.__file__).resolve().parents[2] / "config"
    if manifest.is_source_build(update.installed()):
        return checkout
    bundled = paths.BUNDLED / "kraft-default"  # at call time: tests move it
    if bundled.is_dir():
        return bundled
    if (checkout / ".kraft" / "addon.json").is_file():
        return checkout
    raise FileNotFoundError("this Kraft was built without its kraft-default; reinstall it")


_memo: tuple[tuple, fetch.Extracted] | None = None


def _stamp(root: Path) -> tuple:
    """(path, mtime_ns, size, ino) of every file `extract_dir` could read."""
    out = [str(root)]
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [d for d in dirnames if d in ("chains", "skills", ".kraft")]
        for name in filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            out.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
    return tuple(sorted(out, key=str))


def extracted() -> fetch.Extracted:
    """The built-in as `fetch.extract_dir` reads it, memoized per process until
    a file's stamp changes. Hard links are fine: the package is not an operator
    directory, and uv may link its files from a cache."""
    global _memo
    root = source_dir()
    stamp = _stamp(root)
    if _memo is None or _memo[0] != stamp:
        _memo = (stamp, fetch.extract_dir(root, allow_hardlinks=True))
    return _memo[1]


def required() -> dict[str, tuple[str, ...]]:
    """The harnesses and agent profiles the built-in's manifest requires."""
    requires = json.loads(extracted().files[".kraft/addon.json"][1]).get("requires", {})
    return {k: tuple(requires.get(k, ())) for k in ("harnesses", "profiles")}


def harness_fallback(data: object) -> tuple[object, tuple[str, ...]]:
    """`data` (a parsed `harnesses.yaml`) with each harness and profile the
    built-in requires, and the file lacks, filled in from the packaged
    `harnesses.yaml`; and the profile names filled. An operator's own definition is
    never replaced, and nothing is written to their file."""
    if not isinstance(data, dict):
        return data, ()
    try:
        packaged = yaml.safe_load((source_dir() / "harnesses.yaml").read_text()) or {}
        needs = required()
    except (OSError, fetch.AddonRefused, KeyError):
        return data, ()  # no source: the built-in is left out, and fills nothing
    out, filled = dict(data), []
    for section, names in needs.items():
        have = out.get(section)
        if have is not None and not isinstance(have, dict):
            continue  # the file's own error to name
        have = have or {}
        try:
            missing = {n: packaged[section][n] for n in names if n not in have}
        except (KeyError, TypeError):
            return data, ()  # a packaged file that lacks what the manifest requires
        if missing:
            out[section] = {**have, **missing}
            if section == "profiles":  # the label is for profiles only
                filled += missing
    return out, tuple(filled)


def is_tree(chain) -> bool:
    """Whether an add-on's `inherit.ancestry` (itself first) ends at the built-in."""
    return bool(chain) and chain[-1].id == ID


SHIPS_IT = (
    "Kraft now ships kraft-default itself; run `kraft admin addon update` on what builds on it, "
    "then `kraft admin addon uninstall {id}`"
)
REFUSED = "kraft-default@kraft comes with Kraft; upgrading Kraft updates it"
COLLECTION_REFUSED = "the collection name 'kraft' is Kraft's own"


def is_builtin(addon) -> bool:
    return addon.id == ID


def store_name() -> str:
    return fetch.digest(extracted().files).removeprefix("sha256:")


def entry(store: Path):
    """The built-in as `load.InstalledAddon`, its store named by content under
    `store`. Writes nothing."""
    from kraft.addons import load

    return load.InstalledAddon(
        ID,
        "kraft-default",
        NAMESPACE,
        update.installed(),
        store / store_name(),
        source="./config",
    )


def ensure_store(addons_root: Path, *, wait: float | None = None) -> Path:
    """The current store's directory, written first if it is missing. Never
    in a worker, which only reads."""
    from kraft.addons import update as addon_update

    final = addons_root / "store" / store_name()
    if final.is_dir():
        return final
    if os.environ.get("KRAFT_WORK_ITEM_ID"):
        raise RuntimeError(
            f"the kraft-default store {final.name} is missing; a worker does not write it"
        )
    with addon_update.write_lock(addons_root, wait=wait):
        return fetch.write_store(addons_root, extracted())


# --- seed (Task 6) ---
# --- update preview (Tasks 7-8) ---
# --- doctor support (Task 9) ---
