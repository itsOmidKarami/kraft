"""Is this Kraft behind, and what would replace it.

Every caller of `latest()` is on a path that must work with no network: two of
them are `admin health` and `admin doctor`, which exist to diagnose a broken
machine, and the third is server startup. So nothing here raises and nothing
here blocks for longer than its timeout. A failure is `None`, which reads as
"no update known" everywhere it lands.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from kraft.paths import default_run_dir

#: The project's releases, newest first. Hard-coded rather than configurable:
#: an install pointed at somebody else's release feed is a way to be handed a
#: different program, not a feature anyone asked for.
RELEASES_URL = "https://api.github.com/repos/itsOmidKarami/kraft/releases"

#: One check a day. The thing being watched moves on the order of weeks, and the
#: cost of being a day late is a notice that appears tomorrow instead of today.
CACHE_TTL = 86_400

#: Long enough for a slow link, short enough that a server start on a machine
#: with no route out is not something anybody times.
TIMEOUT = 2.0

#: The wheel download in `perform`. Someone asked for it and is watching, and
#: a multi-megabyte wheel through `glab api` does not fit in `TIMEOUT`.
DOWNLOAD_TIMEOUT = 120.0


@dataclass(frozen=True)
class Release:
    tag: str
    wheel_url: str


def installed() -> str:
    """This Kraft's version, or the answer for a checkout that was never installed."""
    try:
        return _pkg_version("kraft")
    except PackageNotFoundError:
        return "0.0.0+source"


def _cache_path():
    return default_run_dir() / "update-check.json"


def _request(url: str, timeout: float) -> bytes:
    """One plain GET, shared by the releases list and the wheel download.

    The project is public, so no authentication needed.
    """
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _fetch(url: str, timeout: float):
    """Split out so tests can replace the network without a fake transport."""
    return json.loads(_request(url, timeout))


def _parse(payload) -> Release | None:
    """The newest published release that actually has a wheel attached.

    Three things disqualify an entry, and the feed is walked rather than
    indexed at [0] because any of them can be newest: a draft (visible only to
    people with push access, and never installable), a prerelease, and a
    release with no wheel — a tag pushed by hand, or a release job that failed
    after creating one.
    """
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("draft") or entry.get("prerelease"):
            continue
        tag = entry.get("tag_name")
        assets = entry.get("assets") or []
        wheel = next(
            (
                asset.get("browser_download_url")
                for asset in assets
                if isinstance(asset, dict) and str(asset.get("name", "")).endswith(".whl")
            ),
            None,
        )
        if tag and wheel:
            return Release(tag=tag, wheel_url=wheel)
    return None


def _read_cache(now: float) -> Release | None:
    try:
        blob = json.loads(_cache_path().read_text())
        if now - float(blob["checked_at"]) >= CACHE_TTL:
            return None
        return Release(tag=blob["tag"], wheel_url=blob["wheel_url"])
    except OSError, ValueError, KeyError, TypeError:
        return None


def _write_cache(release: Release, now: float) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tag": release.tag, "wheel_url": release.wheel_url, "checked_at": now})
        )
    except OSError:
        # A read-only or full run dir costs a re-check next time, nothing more.
        pass


def latest(*, force: bool = False) -> Release | None:
    """The newest installable release, or `None` if that cannot be established."""
    now = time.time()
    if not force:
        cached = _read_cache(now)
        if cached is not None:
            return cached
    try:
        release = _parse(_fetch(RELEASES_URL, TIMEOUT))
    except Exception:  # noqa: BLE001
        # Deliberately bare: httpx raises a dozen types, json another, and a
        # version check is never worth turning a working command into a
        # traceback. There is nothing this function could do with the
        # distinction that "no update known" does not already cover.
        return None
    if release is not None:
        _write_cache(release, now)
    return release


def _parts(raw: str) -> tuple[int, ...]:
    """The leading numeric components of a version, as integers.

    ponytail: naive dotted-int compare, not PEP 440. It is right for the shapes
    that exist here - `0.4.0` from a tag and `0.3.1.dev4+g1a2b3c` from a dev
    build, where the dev build's base is correctly the *higher* version. Swap in
    `packaging.version` the day a release carries an rc or a post suffix.
    """
    match = re.match(r"\d+(\.\d+)*", raw.lstrip("v"))
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()


def is_behind(release: Release | None) -> bool:
    if release is None:
        return False
    there = _parts(release.tag)
    return bool(there) and _parts(installed()) < there


def perform(release: Release, *, run=None) -> int:
    """Replace this install with `release`. Returns the installer's exit code.

    `uv tool install --force` is the same command `just install` ends with, so
    an updated Kraft is byte-identical to a freshly installed one rather than
    something only this path can produce.

    `release.wheel_url` is a plain public URL now, but it is still fetched here
    rather than handed to `uv`, so that one code path downloads every wheel.
    """
    import tempfile
    from pathlib import Path

    run = run or subprocess.run
    with tempfile.TemporaryDirectory() as tmpdir:
        wheel_path = Path(tmpdir) / release.wheel_url.rsplit("/", 1)[-1]
        wheel_path.write_bytes(_request(release.wheel_url, DOWNLOAD_TIMEOUT))
        command = ["uv", "tool", "install", "--force", "--from", str(wheel_path), "kraft"]
        try:
            return run(command).returncode
        except FileNotFoundError:
            raise SystemExit(
                "kraft admin update: `uv` is not on PATH. Install it, or run this yourself:\n"
                f"  {' '.join(command)}"
            ) from None
