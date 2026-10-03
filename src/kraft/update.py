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
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from kraft.paths import default_run_dir

#: The project's releases, newest first. Hard-coded rather than configurable:
#: an install pointed at somebody else's release feed is a way to be handed a
#: different program, not a feature anyone asked for.
RELEASES_URL = "https://api.github.com/repos/itsOmidKarami/kraft/releases"

#: One check a day. The thing being watched moves on the order of weeks, and the
#: cost of being a day late is a notice that appears tomorrow instead of today.
CACHE_TTL = 86_400

#: How long a check that failed (no route, a bad feed, no usable release) is
#: not repeated. Shorter than `CACHE_TTL`, since the cause is often a link that
#: comes back, but long enough that an offline host does not pay `TIMEOUT` on
#: every page load.
RETRY_AFTER = 3_600

#: Long enough for a slow link, short enough that a server start on a machine
#: with no route out is not something anybody times.
TIMEOUT = 2.0


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
    """One plain GET, for the releases list.

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


def _load() -> dict | None:
    """The cache file as an object, or None when it is missing, unreadable, or
    valid JSON of another shape (`[]`, `null`, `1`): all of them a miss."""
    try:
        blob = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return None
    return blob if isinstance(blob, dict) else None


def _read_cache(now: float, channel: str) -> Release | None:
    blob = _load()
    try:
        if blob is None:
            return None
        if blob.get("channel", "stable") != channel or now - float(blob["checked_at"]) >= CACHE_TTL:
            return None
        return Release(tag=blob["tag"], wheel_url=blob["wheel_url"])
    except (ValueError, KeyError, TypeError):
        return None


def _recently_failed(now: float, channel: str) -> bool:
    """True when a check on `channel` failed less than `RETRY_AFTER` ago."""
    blob = _load()
    try:
        if blob is None or blob.get("channel", "stable") != channel:
            return False
        return 0 <= now - float(blob["failed_at"]) < RETRY_AFTER
    except (ValueError, KeyError, TypeError):
        return False


def _store(blob: dict) -> None:
    """Replace the cache file whole: a reader (another tab's request, `kraft
    admin start`) sees the old file or the new one, never half of one, which
    it would take for a miss and a failure write would then replace."""
    path = _cache_path()
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A name of its own per write: the server checks from several threads
        # at once, and two writers sharing one temp file would swap in half of it.
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        tmp = Path(name)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(blob))
        os.replace(tmp, path)
    except OSError:
        # A read-only or full run dir costs a re-check next time, nothing more.
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _write_failure(now: float, channel: str) -> None:
    """Remember that a check just failed, keeping the last good one's fields.

    `last_checked` reads `checked_at`, which stays the time of the last
    success; the failure rides beside it as `failed_at`.
    """
    blob = _load()
    if blob is None or blob.get("channel", "stable") != channel:
        blob = {"channel": channel}
    blob["failed_at"] = now
    _store(blob)


def _write_cache(release: Release, now: float, channel: str) -> None:
    _store(
        {
            "tag": release.tag,
            "wheel_url": release.wheel_url,
            "channel": channel,
            "checked_at": now,
        }
    )


def last_checked(channel: str = "stable") -> float | None:
    """When a check on `channel` last succeeded, however long ago; None if never."""
    blob = _load()
    try:
        return (
            float(blob["checked_at"]) if blob and blob.get("channel", "stable") == channel else None
        )
    except (ValueError, KeyError, TypeError):
        return None


def latest(
    *, force: bool = False, channel: str = "stable", cache_only: bool = False
) -> Release | None:
    """The newest installable release in `channel`, or `None` if that cannot be established.

    A failed check is remembered for `RETRY_AFTER`, so it is not retried (and
    its timeout not paid again) until then; `force` ignores that. `cache_only`
    never fetches: a cold cache is `None`.
    """
    now = time.time()
    if not force:
        cached = _read_cache(now, channel)
        if cached is not None:
            return cached
        if cache_only or _recently_failed(now, channel):
            return None
    try:
        # The most a page holds: GitHub's default 30 is one long pre-release
        # cycle, which would push the newest final off it.
        release = _parse(_fetch(f"{RELEASES_URL}?per_page=100", TIMEOUT), channel)
    except Exception:  # noqa: BLE001
        # Deliberately bare: httpx raises a dozen types, json another, and a
        # version check is never worth turning a working command into a
        # traceback. There is nothing this function could do with the
        # distinction that "no update known" does not already cover.
        _write_failure(now, channel)
        return None
    if release is None:
        _write_failure(now, channel)
    else:
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


def is_older(version: str, than: str) -> bool:
    """True when release `version` comes before release `than`: an older
    Kraft installed under a newer running server, as a rollback leaves it.
    False when either is not a release: a source checkout (`0.0.0+source`)
    ranks below everything and is no rollback."""
    a, b = _parts(version), _parts(than)
    return a[:3] not in ((), (0, 0, 0)) and bool(b) and a < b


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


#: The package this project publishes, on PyPI and in its release wheels.
PACKAGE = "kraft-sdlc"


def install_kind() -> str:
    """Which installer made this Kraft: `brew`, `uv` (a uv tool, which the
    install script makes too), `pipx`, `source` (an editable checkout), or
    `pip` for any other environment.

    Read off this process's own environment, as `shadowing_kraft` reads
    which `kraft` this is: uv and pipx each leave a file in the venv they
    made, and Homebrew's venv has its own path shape."""
    import json
    from importlib.metadata import PackageNotFoundError, distribution

    if _is_homebrew_install():
        return "brew"
    prefix = Path(sys.prefix)
    if (prefix / "uv-receipt.toml").is_file():
        return "uv"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    try:
        direct = json.loads(distribution(PACKAGE).read_text("direct_url.json") or "{}")
    except (PackageNotFoundError, ValueError):
        direct = {}
    if (direct.get("dir_info") or {}).get("editable"):
        return "source"
    return "pip"


def _install_command(kind: str, wanted: str) -> str:
    """The command that installs `wanted` (`requirement`'s shape) into this
    Kraft the way `kind` (`install_kind`) installed it: `uv`, `pipx` or
    `pip`."""
    import importlib.util

    if kind == "uv":
        return f'uv tool install --force --python {_python()} "{wanted}"'
    if kind == "pipx":
        # The interpreter pipx's venv was made from, as named, never resolved: a
        # resolved path names one patch release, which an upgrade of that Python
        # then leaves behind and a cleanup deletes.
        python = getattr(sys, "_base_executable", None) or sys.executable
        return f'pipx install --force --python {python} "{wanted}"'
    # A venv uv made has no pip of its own.
    pip = (
        f"{sys.executable} -m pip install --upgrade"
        if importlib.util.find_spec("pip") is not None
        else f"uv pip install --python {sys.executable} --upgrade"
    )
    return f'{pip} "{wanted}"'


def _not_by_uv(kind: str, release: Release) -> str:
    """Why `perform` will not update a pipx, pip or source install, and the
    command that will. A `uv tool install` there adds a second copy and
    leaves the `kraft` on PATH, and every MCP server and hook that runs it,
    on the old one."""
    command = (
        "git pull, then just setup (or just install for the `kraft` command)"
        if kind == "source"
        else _install_command(kind, requirement(release))
    )
    where = {
        "pipx": "with pipx",
        "pip": f"with pip, into {sys.prefix}",
        "source": "from a source checkout",
    }[kind]
    return (
        f"kraft admin update: this Kraft was installed {where}. Updating it with uv "
        "would add a second copy, and the `kraft` you run would stay as it is. "
        f"Update it the way it was installed:\n  {command}"
    )


def add_extra_hint(extra: str) -> str:
    """How to add `extra` to this install: the command that reinstalls this
    very version (`==`) on this very Python, with the extras it already has,
    the way it was installed (`install_kind`). An unpinned `uv tool install
    'kraft-sdlc[vector]'` took the newest final release, a downgrade from a
    release candidate the next start refused over its newer database, and
    moved the tool to uv's default Python; on a pip, pipx or Homebrew
    install it added a second Kraft beside the one that runs."""
    kind = install_kind()
    if kind == "brew":
        return (
            f"Homebrew's kraft formula does not ship the '{extra}' extra; "
            f"see Enable vector search in the install docs"
        )
    if kind == "source":
        return f"`uv sync --extra {extra}` in the checkout (`just setup-vector`)"
    extras = sorted({*installed_extras(), extra})
    wanted = f"{PACKAGE}[{','.join(extras)}]=={installed()}"
    return f"`{_install_command(kind, wanted)}`"


def _receipt() -> dict:
    """uv's record of how this tool was installed, or {} when it is not a uv tool."""
    import tomllib

    try:
        return tomllib.loads((Path(sys.prefix) / "uv-receipt.toml").read_text()).get("tool") or {}
    except (OSError, ValueError):
        return {}


def installed_extras() -> list[str]:
    """The extras this install was made with, so an update keeps them.

    uv's receipt names them for a uv tool. `vector` is also read off the
    import it brings, which covers an install uv no longer records the
    request for: every 1.4 `kraft admin update` left a receipt naming only a
    temporary wheel."""
    import importlib.util

    found = set()
    for requirement in _receipt().get("requirements") or []:
        if isinstance(requirement, dict) and requirement.get("name") == PACKAGE:
            found.update(str(extra) for extra in requirement.get("extras") or [])
    if importlib.util.find_spec("fastembed") is not None:
        found.add("vector")
    return sorted(found)


def _python() -> str:
    """The interpreter an update installs on: the one the user chose, if uv
    recorded it, else the running one's minor version ("3.13"). Never the
    tool venv's own `bin/python`, which the reinstall replaces, and never
    the running interpreter's resolved path: that names one patch release
    (`cpython-3.13.12-…`), so the tool stayed on it after `uv python
    upgrade`, and stopped running once that patch was uninstalled. A plain
    `uv tool install`, install.sh and every 1.4 update record no Python."""
    chosen = _receipt().get("python")
    if isinstance(chosen, str) and chosen:
        # A version ("3.12") as is. A path's directory, not the path: the
        # venv's `python` is a link out of it.
        where = os.path.realpath(os.path.dirname(chosen)) + os.sep
        if os.sep not in chosen or not where.startswith(os.path.realpath(sys.prefix) + os.sep):
            return chosen
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def requirement(release: Release) -> str:
    """`kraft-sdlc[extras]==X.Y.Z` for `release`, the same shape the install
    docs give for pinning a version."""
    extras = installed_extras()
    named = f"[{','.join(extras)}]" if extras else ""
    return f"{PACKAGE}{named}=={release.tag.removeprefix('v')}"


def perform(release: Release, *, run=None) -> int:
    """Replace this install with `release`. Returns the installer's exit code.

    Installed by Homebrew -> updated by Homebrew: `brew upgrade` reads the
    formula this project's own release workflow bumps, and a `uv tool
    install` here would just leave a second, unrelated `kraft` on PATH
    instead of touching the Homebrew one. ponytail: doesn't thread `--force`
    through to `brew reinstall` for the "already current, force anyway" case
    -- that's a dev-only edge of `kraft admin update --force`, and `brew
    upgrade` no-opping on an up-to-date formula is a fine ceiling for it.

    Installed by pipx, pip or from a checkout -> refused, with the command
    that does update it (`install_kind`).

    Otherwise, `uv tool install --force` of `kraft-sdlc[extras]==X` from the
    package index, on the same Python: the extras and the interpreter the
    user installed with survive the update, and uv's record of the tool
    names the index, which stays valid. The record pins that version
    (`==`), as installing a release by number does, so `uv tool upgrade`
    leaves it where it is; `kraft admin update` is what moves it. The
    release workflow creates the GitHub
    release before it publishes to PyPI, so in that window the index has no
    such version, and the release's wheel is installed by its URL instead.
    uv records that URL, which stays valid, where the temporary file this
    used to download the wheel to did not. Nothing checks the wheel that
    the index install does not, so it is the fallback, not the default.
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

    if (kind := install_kind()) != "uv":
        raise SystemExit(_not_by_uv(kind, release))

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

    wanted = requirement(release)
    base = ["uv", "tool", "install", "--force", "--python", _python()]
    command = [*base, wanted]
    try:
        code = run(command).returncode
    except FileNotFoundError:
        raise SystemExit(
            "kraft admin update: `uv` is not on PATH. Install it, or run this yourself:\n"
            f"  {' '.join(command)}"
        ) from None
    if code == 0:
        return 0
    print(
        f"kraft admin update: could not install {wanted} from the package index; "
        f"installing the release's wheel instead: {release.wheel_url}",
        file=sys.stderr,
    )
    extras = wanted[len(PACKAGE) :].split("==", 1)[0]
    return run([*base, f"{PACKAGE}{extras} @ {release.wheel_url}"]).returncode
