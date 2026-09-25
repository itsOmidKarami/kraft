"""Is this Kraft behind, and what would replace it.

Every caller of `latest()` is on a path that must work with no network: two of
them are `admin health` and `admin doctor`, which exist to diagnose a broken
machine, and the third is server startup. So nothing here raises and nothing
here blocks for longer than its timeout. A failure is `None`, which reads as
"no update known" everywhere it lands.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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


#: Pre-release marks in ascending order; a final release outranks all of them.
_MARKS = ("a", "b", "rc")

#: How far down the stability ladder each channel reaches. `beta` takes betas,
#: rcs and finals; `alpha` takes everything.
CHANNELS = {"stable": 3, "rc": 2, "beta": 1, "alpha": 0}

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")


def _rank(raw: str) -> tuple[int, ...] | None:
    """Sort key for a release tag (`v1.3.0`, `v1.3.0rc2`), or None for any other shape.

    Pre-releases sort below their own final, as PEP 440 does. Dev builds and
    other shapes are not tags anyone publishes, so they are simply not ranked.
    """
    m = _VERSION.fullmatch(raw)
    if not m:
        return None
    mark = _MARKS.index(m[4]) if m[4] else len(_MARKS)
    return (int(m[1]), int(m[2]), int(m[3]), mark, int(m[5] or 0))


@dataclass(frozen=True)
class Release:
    tag: str
    wheel_url: str


def installed() -> str:
    """This Kraft's version, or the answer for a checkout that was never installed."""
    try:
        return _pkg_version("kraft-sdlc")
    except PackageNotFoundError:
        return "0.0.0+source"


def shadowing_kraft() -> str | None:
    """The `kraft` PATH resolves to, when that is not this install; else None.

    Every agent registration runs `kraft admin mcp` by name (`init.py`, the
    plugin manifest), so a second, older install earlier on PATH is what those
    sessions get -- `kraft admin update` replacing this one changes nothing
    they run (Kraft-xs3ri: a Homebrew 0.65.0 ahead of a uv 0.76.2 kept MCP's
    `ensure_repo` on a fix three releases old). A console script lives next to
    its venv's interpreter, so "this install" is `sys.executable`'s directory.
    None too when nothing named `kraft` is on PATH: there is nothing to shadow.
    """
    found = shutil.which("kraft")
    if found is None:
        return None
    here = os.path.realpath(os.path.dirname(sys.executable))
    return None if os.path.dirname(os.path.realpath(found)) == here else found


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


def _parse(payload, channel: str = "stable") -> Release | None:
    """The newest published release in `channel` that actually has a wheel attached.

    Three things disqualify an entry, and the feed is walked rather than
    indexed at [0] because any of them can be newest: a draft (visible only to
    people with push access, and never installable), a prerelease less stable
    than `channel` allows, and a release with no wheel — a tag pushed by hand,
    or a release job that failed after creating one. Of what is left the
    highest version wins, not the most recently published.
    """
    if not isinstance(payload, list):
        return None
    best: tuple[tuple[int, ...], Release] | None = None
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("draft"):
            continue
        tag = entry.get("tag_name")
        rank = _rank(str(tag))
        if rank is None or rank[3] < CHANNELS[channel]:
            continue
        assets = entry.get("assets") or []
        wheel = next(
            (
                asset.get("browser_download_url")
                for asset in assets
                if isinstance(asset, dict) and str(asset.get("name", "")).endswith(".whl")
            ),
            None,
        )
        if wheel and (best is None or rank > best[0]):
            best = (rank, Release(tag=tag, wheel_url=wheel))
    return best[1] if best else None


def _read_cache(now: float, channel: str) -> Release | None:
    try:
        blob = json.loads(_cache_path().read_text())
        if blob.get("channel", "stable") != channel or now - float(blob["checked_at"]) >= CACHE_TTL:
            return None
        return Release(tag=blob["tag"], wheel_url=blob["wheel_url"])
    except OSError, ValueError, KeyError, TypeError:
        return None


def _write_cache(release: Release, now: float, channel: str) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tag": release.tag,
                    "wheel_url": release.wheel_url,
                    "channel": channel,
                    "checked_at": now,
                }
            )
        )
    except OSError:
        # A read-only or full run dir costs a re-check next time, nothing more.
        pass


def latest(*, force: bool = False, channel: str = "stable") -> Release | None:
    """The newest installable release in `channel`, or `None` if that cannot be established."""
    now = time.time()
    if not force:
        cached = _read_cache(now, channel)
        if cached is not None:
            return cached
    try:
        release = _parse(_fetch(RELEASES_URL, TIMEOUT), channel)
    except Exception:  # noqa: BLE001
        # Deliberately bare: httpx raises a dozen types, json another, and a
        # version check is never worth turning a working command into a
        # traceback. There is nothing this function could do with the
        # distinction that "no update known" does not already cover.
        return None
    if release is not None:
        _write_cache(release, now, channel)
    return release


def _parts(raw: str) -> tuple[int, ...]:
    """Sort key of an installed version or tag; empty when it is not a release.

    A dev build (`0.3.1.dev4+g1a2b3c`) keeps its base's release numbers and
    ranks as that final, so it is correctly *ahead* of the last release.
    """
    if rank := _rank(raw):
        return rank
    base = re.match(r"v?\d+\.\d+\.\d+", raw)
    return _rank(base[0]) or () if base else ()


def is_behind(release: Release | None) -> bool:
    if release is None:
        return False
    there = _parts(release.tag)
    return bool(there) and _parts(installed()) < there


def channel_of(version: str) -> str:
    """The channel a version was published on: `1.3.0rc1` -> rc, a final -> stable."""
    m = _VERSION.fullmatch(version)
    return {"a": "alpha", "b": "beta", "rc": "rc"}.get(m[4] if m else "", "stable")


def _is_homebrew_install() -> bool:
    """True when this process is the venv Homebrew's `kraft` formula built.

    Homebrew's `virtualenv_create` puts the venv at
    `<prefix>/Cellar/kraft/<version>/libexec`, so a running kraft's
    `sys.prefix` contains that "/Cellar/kraft/" segment if and only if
    Homebrew is what installed it -- `uv tool`, pip, and a source checkout
    never produce that path shape.
    """
    return "/Cellar/kraft/" in sys.prefix


def _stale_kraft_tool(run) -> bool:
    """Whether uv still holds a `kraft` tool: this package's name before the
    kraft -> kraft-sdlc PyPI rename (Kraft-rswxq). Both receipts claim the
    `kraft` command, so uninstalling the stale one deletes it for both.
    False when uv cannot say -- `perform` reports a missing uv itself."""
    try:
        listing = run(["uv", "tool", "list"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    out = getattr(listing, "stdout", "") or ""
    return listing.returncode == 0 and re.search(r"^kraft v", out, re.MULTILINE) is not None


def perform(release: Release, *, run=None) -> int:
    """Replace this install with `release`. Returns the installer's exit code.

    Installed by Homebrew -> updated by Homebrew: `brew upgrade` reads the
    formula this project's own release workflow bumps, and a `uv tool
    install` here would just leave a second, unrelated `kraft` on PATH
    instead of touching the Homebrew one. ponytail: doesn't thread `--force`
    through to `brew reinstall` for the "already current, force anyway" case
    -- that's a dev-only edge of `kraft admin update --force`, and `brew
    upgrade` no-opping on an up-to-date formula is a fine ceiling for it.

    Otherwise, `uv tool install --force` is the same command `just install`
    ends with, so an updated Kraft is byte-identical to a freshly installed
    one rather than something only this path can produce.

    `release.wheel_url` is a plain public URL now, but it is still fetched here
    rather than handed to `uv`, so that one code path downloads every wheel.
    """
    run = run or subprocess.run
    if _is_homebrew_install():
        command = ["brew", "upgrade", "kraft"]
        try:
            return run(command).returncode
        except FileNotFoundError:
            raise SystemExit(
                "kraft admin update: this is a Homebrew install, but `brew` is not "
                "on PATH. Install it, or run this yourself:\n"
                f"  {' '.join(command)}"
            ) from None

    if _stale_kraft_tool(run):
        # ponytail: told, not migrated -- a `kraft` receipt could be another
        # project's tool of that name, and uninstalling it is not ours to do.
        raise SystemExit(
            "kraft admin update: uv still has a `kraft` tool from before the rename to "
            "kraft-sdlc, and both claim the `kraft` command. Uninstalling it removes that "
            "command too, so run both, in order:\n"
            "  uv tool uninstall kraft\n"
            "  uv tool install --force --reinstall kraft-sdlc"
        )

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        wheel_path = Path(tmpdir) / release.wheel_url.rsplit("/", 1)[-1]
        wheel_path.write_bytes(_request(release.wheel_url, DOWNLOAD_TIMEOUT))
        command = ["uv", "tool", "install", "--force", "--from", str(wheel_path), "kraft-sdlc"]
        try:
            return run(command).returncode
        except FileNotFoundError:
            raise SystemExit(
                "kraft admin update: `uv` is not on PATH. Install it, or run this yourself:\n"
                f"  {' '.join(command)}"
            ) from None
