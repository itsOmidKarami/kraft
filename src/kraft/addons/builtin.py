"""The built-in `kraft-default@kraft` add-on: its identity and where its files
come from. Kraft's own chains and library are read from the installed package,
not from a home."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

import kraft
from kraft import paths, update, yamlload
from kraft.addons import fetch, manifest

ID = "kraft-default@kraft"
NAMESPACE = "kraft-default"
COLLECTION = "kraft"


def source_dir() -> Path:
    """Where the built-in's files are: the checkout's `config/` for a source
    build (even beside a stale bundle), else the wheel's bundle, else a
    release-tag checkout's `config/`. A dev version installed from a checkout
    (`just install` off a tag) has no checkout beside it: it takes the bundle."""
    checkout = Path(kraft.__file__).resolve().parents[2] / "config"
    real_checkout = (checkout / ".kraft" / "addon.json").is_file()
    if manifest.is_source_build(update.installed()) and real_checkout:
        return checkout
    bundled = paths.BUNDLED / "kraft-default"  # at call time: tests move it
    if bundled.is_dir():
        return bundled
    if real_checkout:
        return checkout
    raise FileNotFoundError("this Kraft was built without its kraft-default; reinstall it")


_memo: tuple[Path, tuple, fetch.Extracted] | None = None


def frozen() -> bool:
    """An installed release reads its bundle once: `uv tool install` replaces the
    files in place under a server that keeps running, and that server's code must
    not read the next release's chains. A checkout re-reads its edits."""
    return not manifest.is_source_build(update.installed())


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
    a file's stamp changes (never, for an installed release: see `frozen`). Hard
    links are fine: the package is not an operator directory, and uv may link its
    files from a cache."""
    global _memo
    root = source_dir()
    if _memo is not None and _memo[0] == root and frozen():
        return _memo[2]
    stamp = _stamp(root)
    if _memo is None or _memo[:2] != (root, stamp):
        _memo = (root, stamp, fetch.extract_dir(root, allow_hardlinks=True))
    return _memo[2]


_harnesses_memo: tuple[Path, object] | None = None


def _packaged_harnesses() -> object:
    """The packaged `harnesses.yaml` the fallback fills from, frozen with the
    bundle for an installed release (see `frozen`)."""
    global _harnesses_memo
    root = source_dir()
    if _harnesses_memo is None or _harnesses_memo[0] != root or not frozen():
        _harnesses_memo = (root, yamlload.safe_load((root / "harnesses.yaml").read_text()) or {})
    return copy.deepcopy(_harnesses_memo[1])


def required() -> dict[str, tuple[str, ...]]:
    """The harnesses and agent profiles the built-in's manifest requires."""
    requires = json.loads(extracted().files[".kraft/addon.json"][1]).get("requires", {})
    return {k: tuple(requires.get(k, ())) for k in ("harnesses", "profiles")}


def harness_fallback(data: object) -> tuple[object, tuple[str, ...]]:
    """`data` (a parsed `harnesses.yaml`) with each harness and profile the
    built-in requires, and the file lacks, filled in from the packaged
    `harnesses.yaml`; and the profile names filled. An operator's own definition is
    never replaced, and nothing is written to their file."""
    out, filled = _fill(data)
    return out, filled["profiles"]


def filled_harnesses(data: object) -> tuple[str, ...]:
    """The harness names `harness_fallback` fills in (profiles are its second answer)."""
    return _fill(data)[1]["harnesses"]


def _fill(data: object) -> tuple[object, dict[str, tuple[str, ...]]]:
    none = {"harnesses": (), "profiles": ()}
    if not isinstance(data, dict):
        return data, none
    try:
        packaged = _packaged_harnesses()
        needs = required()
    except (
        OSError,
        fetch.AddonRefused,
        KeyError,
        ValueError,
        yaml.YAMLError,
        AttributeError,
        TypeError,
    ):
        return data, none  # no source: the built-in is left out, and fills nothing
    out, filled = dict(data), dict(none)
    for section, names in needs.items():
        have = out.get(section)
        if have is not None and not isinstance(have, dict):
            continue  # the file's own error to name
        have = have or {}
        try:
            missing = {n: packaged[section][n] for n in names if n not in have}
        except (KeyError, TypeError):
            return data, none  # a packaged file that lacks what the manifest requires
        if missing:
            out[section] = {**have, **missing}
            filled[section] = tuple(missing)
    return out, filled


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
    """The current store's directory, written first if it is missing or fails
    verification (`write_store` replaces it). Never in a worker, which only
    reads."""
    from kraft.addons import update as addon_update

    final = addons_root / "store" / store_name()
    worker = bool(os.environ.get("KRAFT_WORK_ITEM_ID"))
    if final.is_dir():
        try:
            fetch.verify_store(final)
            return final
        except (fetch.AddonRefused, OSError):
            if worker:
                return final  # a worker only reads; the load judges it as it is
    if worker:
        raise RuntimeError(
            f"the kraft-default store {final.name} is missing; a worker does not write it"
        )
    with addon_update.write_lock(addons_root, wait=wait):
        return fetch.write_store(addons_root, extracted())


# --- seed (Task 6) ---
_CHAIN_SEED = """\
# Kraft's own `{id}` chain. It comes from the kraft-default add-on that ships
# with Kraft, so upgrading Kraft updates it. To change a node, add it under
# `overrides:` here; a library.yaml entry of your own does not change this chain.
id: {id}
extends: kraft-default:{id}
"""

_LIBRARY_SEED = """\
# Your own tasks, steps, nodes and steering. Kraft's own come from the
# kraft-default add-on that ships with Kraft. An entry here changes only the
# chains that name it; to change what `default` runs, override its node in
# chains/default.yaml.
tasks: {}
"""


def write_thin_seed(target: Path) -> None:
    """Write a new home's chains and library: one `extends: kraft-default:<id>`
    chain per built-in chain and an empty library, so releases flow underneath
    any edit. Seeding a home that has none is the only write Kraft makes to one."""
    chains = target / "chains"
    chains.mkdir(parents=True, exist_ok=True)
    for name in sorted(extracted().files):
        if name.startswith("chains/") and name.endswith(".yaml"):
            chain_id = name.removeprefix("chains/").removesuffix(".yaml")
            (chains / f"{chain_id}.yaml").write_text(_CHAIN_SEED.format(id=chain_id))
    (target / "library.yaml").write_text(_LIBRARY_SEED)


# --- update preview (Tasks 7-8) ---
_WHEEL_DIR = "kraft/_bundled/kraft-default/"


def from_wheel(wheel: Path, scratch: Path) -> fetch.Extracted | None:
    """The built-in a release wheel ships, unpacked under `scratch`; None for a
    wheel with none (an older release). A member that escapes `scratch` is a
    ValueError."""
    import zipfile

    root = scratch / "kraft-default"
    found = False
    with zipfile.ZipFile(wheel) as z:
        members = [i for i in z.infolist() if i.filename.startswith(_WHEEL_DIR) and not i.is_dir()]
        if len(members) > fetch.MAX_FILES or sum(i.file_size for i in members) > fetch.MAX_TOTAL:
            raise ValueError("the wheel's kraft-default is larger than an add-on may be")
        for info in members:
            name = info.filename
            rel = Path(name.removeprefix(_WHEEL_DIR))
            if rel.is_absolute() or ".." in rel.parts or name.startswith("/"):
                raise ValueError(f"the wheel holds an unsafe path: {name}")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            found = True
    return fetch.extract_dir(root, allow_hardlinks=True) if found else None


@dataclass(frozen=True)
class Preview:
    """What installing `release` would change in the built-in. `note` says why
    no comparison was made."""

    review: object | None  # review.Review
    breaks: tuple[str, ...]
    note: str | None


def preview(
    release: update.Release, config_dir: Path, addons_root: Path, *, channel: str = "stable"
) -> Preview:
    """Compare the running built-in with the one `release` ships, before
    anything is installed. Never raises: a failure is a `note`."""
    import tempfile

    from kraft.addons import load
    from kraft.addons import review as addon_review
    from kraft.addons import update as addon_update

    try:
        if not release.wheel_url:
            release = update.release_for(release.tag, channel=channel) or release
            if not release.wheel_url:
                return Preview(None, (), f"cannot compare: no wheel found for {release.tag}")
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            wheel = update.download(release.wheel_url, scratch / "kraft.whl")
            new = from_wheel(wheel, scratch / "wheel")
            if new is None:
                return Preview(None, (), f"{release.tag} ships no built-in kraft-default")
            reviewed = replace(
                addon_review.review(ID, extracted(), new),
                old_version=update.installed(),
                new_version=release.tag.removeprefix("v"),
            )
            found = load.installed(config_dir, addons_root)
            root = fetch.write_scratch(scratch / "store", new)
            after = [replace(a, root=root) if is_builtin(a) else a for a in found]
            broke = addon_update.breaks(
                addon_update.state_of(config_dir, found),
                addon_update.state_of(config_dir, after),
                config_dir,
                NAMESPACE,
            )
            return Preview(reviewed, tuple(broke), None)
    except Exception as exc:  # noqa: BLE001 -- a failed comparison is a note, never a crash
        return Preview(None, (), f"cannot compare: {exc}")


def changed(p: Preview) -> bool:
    """Whether the new built-in differs in a way worth showing (`Review.changed`
    also counts a version bump, which every release has)."""
    return p.review is not None and bool(p.review.reach or p.review.content)


def show(p: Preview) -> str:
    from kraft.addons import review as addon_review

    lines = [addon_review.render(p.review)] if changed(p) else []
    lines += [f"  breaks: {line}" for line in p.breaks]
    if p.note:
        lines.append(f"kraft-default: {p.note}")
    return addon_review.escape("\n".join(lines)) if lines else "kraft-default: unchanged"


def held(p: Preview) -> list[str]:
    """Why an unattended update must not install: a break, or no comparison."""
    if p.breaks:
        return list(p.breaks)
    return [p.note] if p.note and p.note.startswith("cannot compare") else []


def clear(addons_root: Path) -> None:
    (addons_root / "builtin.json").unlink(missing_ok=True)


def needs_answer(p: Preview) -> bool:
    return changed(p) or bool(p.breaks) or bool(p.note)


def record(addons_root: Path, *, status: str, from_: str, to: str, lines: Sequence[str]) -> None:
    """What an unattended update held or changed, for `doctor`. Not operator
    config: it lives beside the stores."""
    addons_root.mkdir(parents=True, exist_ok=True)
    (addons_root / "builtin.json").write_text(
        json.dumps(
            {"status": status, "from": from_, "to": to, "lines": list(lines), "at": time.time()}
        )
    )


def recorded(addons_root: Path) -> dict | None:
    try:
        data = json.loads((addons_root / "builtin.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- doctor support (Task 9) ---
