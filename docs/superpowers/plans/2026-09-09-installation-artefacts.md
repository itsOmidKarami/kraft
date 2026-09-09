# Installation Artefacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A git tag produces an installable Kraft wheel with the SPA in it, and an installed Kraft can say what version it is and when it is behind.

**Architecture:** The version stops being a literal and becomes a function of the git tag (`setuptools-scm`). The SPA copy step moves out of `just install` into a shared `just bundle` that CI also calls. A new `kraft.update` module reads the GitLab Releases API through a 24h disk cache and never raises. The agent skills stop being Python string literals and become files, which lets the same directory serve both `kraft admin init` and a marketplace plugin. A tag pipeline builds, smoke-tests and releases the wheel; a `release::` label on every merge request is what creates the tag.

**Tech Stack:** Python 3.14, setuptools + setuptools-scm, uv, GitLab CI, httpx (already a runtime dependency), pytest, just, npm/vite.

**Spec:** `docs/superpowers/specs/2026-09-09-installation-artefacts-design.md`

## Global Constraints

- Python floor is `>=3.14`. `except OSError, ValueError:` (PEP 758, unparenthesized) is valid here and is the existing style — see `src/kraft/init.py:219`. Do not "fix" it.
- `plugins/kraft-lite/` must stay stdlib-only and run on Python 3.10 (CI job `lite-floor`). Nothing in this plan adds a dependency there.
- Ruff line length is 100. Run `just lint` before every commit.
- No new runtime dependencies. `httpx>=0.28.1` is already in `[project].dependencies` and is the HTTP client to use.
- Do not run the full `just test` — it takes ~14 minutes. Each task names the test files to run.
- Do not push, do not merge, do not create tags. This branch's work is committed only.
- Every check added to `admin doctor` that is informational must set `ok=True`, or it changes doctor's exit code. See `src/kraft/doctor.py:27`.

---

### Task 1: Version from the git tag

**Files:**
- Modify: `pyproject.toml` (the `[build-system]` and `[project]` tables)
- Modify: `.gitlab-ci.yml` (add `GIT_DEPTH: 0` to `lint-and-test`)
- Test: `tests/test_version.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: a real `importlib.metadata.version("kraft")` for every later task. No new Python symbols.

**Context:** `src/kraft/cli.py:240` (`_version()`) and `src/kraft/init.py:174` already read `importlib.metadata`. Neither changes in this task — only the number they read becomes real.

- [ ] **Step 1: Write the failing test**

Create `tests/test_version.py`:

```python
"""The version an installed Kraft reports.

`--version` exists to diagnose an install (cli.py:255), so the number it prints
has to come from the installed distribution rather than a literal that a release
can leave behind.
"""

from __future__ import annotations

from importlib import metadata

from kraft import cli


def test_version_is_not_the_placeholder():
    assert metadata.version("kraft") != "0.0.0"


def test_cli_version_matches_the_installed_distribution():
    assert cli._version() == metadata.version("kraft")


def test_version_survives_not_being_installed(monkeypatch):
    """A source checkout that was never installed answers rather than tracebacks."""

    def missing(_name):
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(cli, "_pkg_version", missing)
    assert cli._version() == "0.0.0+source"
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_version.py -v`
Expected: `test_version_is_not_the_placeholder` FAILS (`'0.0.0' != '0.0.0'`). The other two pass already — that is correct, they are the regression guard for behaviour that must survive this change.

- [ ] **Step 3: Switch pyproject to setuptools-scm**

In `pyproject.toml`, change `[project]`:

```toml
[project]
name = "kraft"
dynamic = ["version"]
description = "Kraft orchestrator walking skeleton"
```

(Delete the `version = "0.0.0"` line. `dynamic` and a static `version` are mutually exclusive — leaving both is a build error, not a warning.)

Change `[build-system]`:

```toml
[build-system]
requires = ["setuptools>=68", "setuptools-scm>=8"]
build-backend = "setuptools.build_meta"
```

Add a new table (anywhere after `[build-system]`):

```toml
# The version is the git tag, so a release cannot ship a number nobody bumped.
# An untagged checkout builds `0.3.1.dev4+g1a2b3c`: the last release, the distance
# from it, and the commit — which is what a bug report actually wants.
[tool.setuptools_scm]
```

- [ ] **Step 4: Reinstall and verify**

```bash
git tag -a v0.1.0 -m "first version" 2>/dev/null || true
uv sync
uv run pytest tests/test_version.py -v
```

Expected: PASS, all three. `uv run python -c "from importlib import metadata; print(metadata.version('kraft'))"` prints `0.1.0` or `0.1.1.devN+g<sha>`.

If it prints `0.1.dev1` with no tag component, the tag is missing from the local history — `git tag` to confirm, and create `v0.1.0` if this repo has never been tagged.

- [ ] **Step 5: Give CI its tags**

`setuptools-scm` reads tags from git history, and a shallow clone has none. In `.gitlab-ci.yml`, add to `lint-and-test`:

```yaml
  variables:
    # setuptools-scm derives the version from tags; a shallow clone has none and
    # every build in it silently versions itself 0.1.dev1.
    GIT_DEPTH: 0
```

(`lite-version` already sets `GIT_DEPTH: 0` for a different reason — copy its placement, not its comment.)

- [ ] **Step 6: Commit**

```bash
just lint
git add pyproject.toml .gitlab-ci.yml tests/test_version.py
git commit -m "feat: derive the version from the git tag

The version was pinned at 0.0.0, so kraft --version diagnosed nothing and
no wheel could carry a meaningful number. setuptools-scm makes the tag the
single source, which removes the bump commit and with it the chance to
forget one."
```

---

### Task 2: `just bundle`, and a doctor check for the bundle

**Files:**
- Modify: `justfile:64-82` (split `install` into `bundle` + `install`)
- Modify: `src/kraft/doctor.py` (add `_bundle_check`, call it from `run_checks`)
- Test: `tests/test_cli_doctor.py` (add two tests)

**Interfaces:**
- Consumes: nothing.
- Produces: `just bundle` (called by Task 8's CI job). `doctor._bundle_check() -> dict` in the `_check()` shape: `{"name", "ok", "detail", "skipped"}`.

**Context:** `src/kraft/paths.py:41` defines `BUNDLED = Path(__file__).parent / "_bundled"`. The `justfile` `install` recipe is the only thing that ever populates it, which is the hole this task half-closes. `doctor.py:33` `run_checks()` is the ordered list to append to.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_doctor.py`:

```python
def test_bundle_check_fails_when_the_spa_is_missing(monkeypatch, tmp_path):
    """A wheel built without `just bundle` serves JSON and no UI. Nothing said so."""
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    row = doctor._bundle_check()
    assert not row["ok"]
    assert "just bundle" in row["detail"]


def test_bundle_check_passes_when_the_spa_is_there(monkeypatch, tmp_path):
    web = tmp_path / "_bundled" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html></html>")
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "_bundled")
    assert doctor._bundle_check()["ok"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cli_doctor.py -k bundle -v`
Expected: FAIL, `AttributeError: module 'kraft.doctor' has no attribute '_bundle_check'`.

- [ ] **Step 3: Add the check**

In `src/kraft/doctor.py`, add after `_agent_check` (around line 193):

```python
def _bundle_check() -> dict:
    """Is the built SPA actually in this install?

    `just bundle` is what puts it there. A wheel built by anything else matches
    the `_bundled/**/*` package-data glob against nothing, and the result is an
    API that serves JSON and no UI — visible only to whoever opens the browser.
    """
    index = BUNDLED / "web" / "index.html"
    if index.is_file():
        return _check("spa bundle", True, str(index.parent))
    return _check("spa bundle", False, f"no SPA at {index} — built without `just bundle`")
```

In `run_checks()`, append it next to `_agent_check()` (doctor.py:50):

```python
    checks.extend(_config_checks())
    checks.append(_agent_check())
    checks.append(_bundle_check())
```

`BUNDLED` is already imported at `doctor.py:20`. Do not re-import it.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_cli_doctor.py -v`
Expected: PASS. The existing `test_doctor_on_a_live_instance_reaches_every_check` still passes — it asserts named checks are present, not the total count.

- [ ] **Step 5: Split the justfile recipe**

Replace the `install` recipe (`justfile:64-82`) with:

```
# ponytail: only a `just bundle` build produces a correct wheel — a bare
# `uv build` still matches the `_bundled/**/*` glob against nothing. The two
# guards are `admin doctor`'s spa bundle check and the release job's smoke
# install; a `build_py` subclass is the upgrade if a wheel is ever built by
# something that calls neither.
# Build the SPA and default config into the package tree. `install` and the
# release pipeline both call this, so the two cannot drift.
bundle:
    cd frontend && npm run build
    rm -rf src/kraft/_bundled
    mkdir -p src/kraft/_bundled
    cp -R frontend/dist src/kraft/_bundled/web
    cp -R templates src/kraft/_bundled/templates
    # never ship a local access.yaml or notify.yaml: one holds this machine's
    # password hash, the other a webhook URL that usually embeds a bearer
    # token. `cp -R` does not know either is secret -- `.gitignore` only keeps
    # them out of the commit, not out of the wheel or the homes it seeds.
    rm -f src/kraft/_bundled/templates/access.yaml
    rm -f src/kraft/_bundled/templates/notify.yaml

# Install `kraft` as a real command (then just run `kraft` from anywhere).
# State lands in ~/.kraft, seeded from templates/ on first run.
install: bundle
    uv tool install --from . kraft --force
    @echo "installed. run: kraft"
```

- [ ] **Step 6: Verify the recipe still installs**

Run: `just install && kraft admin doctor`
Expected: the install succeeds and the `spa bundle` row reads `ok`.

- [ ] **Step 7: Commit**

```bash
just lint
git add justfile src/kraft/doctor.py tests/test_cli_doctor.py
git commit -m "feat: extract just bundle, and let doctor see a missing SPA

The SPA copy lived only in `just install`, so a wheel built any other way
shipped an API with no UI and nothing reported it. The steps are now one
recipe CI can call, and doctor names the failure when they were skipped."
```

---

### Task 3: `kraft.update.latest()` — the version check that cannot fail

**Files:**
- Create: `src/kraft/update.py`
- Test: `tests/test_update.py` (create)

**Interfaces:**
- Consumes: `kraft.paths.default_run_dir()`.
- Produces:
  - `Release` — frozen dataclass, fields `tag: str`, `wheel_url: str`.
  - `installed() -> str` — the installed version, or `"0.0.0+source"`.
  - `latest(*, force: bool = False) -> Release | None` — newest release, cached 24h, `None` on any failure.
  - `is_behind(release: Release | None) -> bool`.
  - `CACHE_TTL: int`, `RELEASES_URL: str`.

**Context:** Three callers in Task 5 sit on paths that must work on a disconnected machine, which is why every failure here returns `None` rather than raising. `src/kraft/client.py` is the house style for httpx use.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update.py`:

```python
"""`kraft.update` — the version check.

Its three callers (admin health, admin doctor, admin start) all sit on paths a
disconnected machine must still walk, so the contract under test is mostly
negative: no failure mode raises, and no failure mode reports an update.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kraft import update

RELEASE_JSON = [
    {
        "tag_name": "v0.4.0",
        "assets": {"links": [{"name": "kraft-0.4.0-py3-none-any.whl", "url": "https://x/w.whl"}]},
    }
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    return tmp_path / "run" / "update-check.json"


def _fetch(payload):
    def fetch(_url, _timeout):
        return payload

    return fetch


def test_latest_reads_the_newest_release(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    release = update.latest()
    assert release.tag == "v0.4.0"
    assert release.wheel_url == "https://x/w.whl"


def test_latest_caches_to_disk(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    assert json.loads(cache.read_text())["tag"] == "v0.4.0"


def test_a_cache_hit_does_not_fetch(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()

    def explode(_url, _timeout):
        raise AssertionError("fetched inside the TTL")

    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest().tag == "v0.4.0"


def test_an_expired_cache_refetches(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    stale = json.loads(cache.read_text())
    stale["checked_at"] = 0
    cache.write_text(json.dumps(stale))
    monkeypatch.setattr(update, "_fetch", _fetch([{"tag_name": "v0.5.0", "assets": {"links": []}}]))
    assert update.latest().tag == "v0.5.0"


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("no network"),
        httpx.ReadTimeout("too slow"),
        ValueError("not json"),
    ],
)
def test_every_transport_failure_is_none_not_an_exception(cache, monkeypatch, boom):
    def fetch(_url, _timeout):
        raise boom

    monkeypatch.setattr(update, "_fetch", fetch)
    assert update.latest() is None


def test_a_garbage_body_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch({"not": "a list"}))
    assert update.latest() is None


def test_an_empty_release_list_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch([]))
    assert update.latest() is None


def test_a_release_with_no_wheel_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch([{"tag_name": "v9.0.0", "assets": {"links": []}}]))
    assert update.latest() is None


@pytest.mark.parametrize(
    ("here", "there", "behind"),
    [
        ("0.3.0", "v0.4.0", True),
        ("0.4.0", "v0.4.0", False),
        ("0.5.0", "v0.4.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.3.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.4.0", True),
        ("0.0.0+source", "v0.4.0", True),
        ("0.4.0", "vnonsense", False),
    ],
)
def test_is_behind_compares_versions(monkeypatch, here, there, behind):
    monkeypatch.setattr(update, "installed", lambda: here)
    assert update.is_behind(update.Release(tag=there, wheel_url="u")) is behind


def test_is_behind_of_nothing_is_false():
    assert update.is_behind(None) is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'kraft.update'`.

- [ ] **Step 3: Write the module**

Create `src/kraft/update.py`:

```python
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
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from kraft.paths import default_run_dir

#: The project's releases, newest first. Hard-coded rather than configurable:
#: an install pointed at somebody else's release feed is a way to be handed a
#: different program, not a feature anyone asked for.
RELEASES_URL = "https://gitlab.com/api/v4/projects/itsOmidKarami%2Fkraft/releases"

#: One check a day. The thing being watched moves on the order of weeks, and the
#: cost of being a day late is a notice that appears tomorrow instead of today.
CACHE_TTL = 86_400

#: Long enough for a slow link, short enough that a server start on a machine
#: with no route out is not something anybody times.
TIMEOUT = 2.0


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


def _fetch(url: str, timeout: float):
    """Split out so tests can replace the network without a fake transport."""
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _parse(payload) -> Release | None:
    """The newest release that actually has a wheel attached.

    A release with no wheel is not installable, so it is not an update — a
    tag pushed by hand, or a release job that failed after creating one.
    """
    if not isinstance(payload, list) or not payload:
        return None
    newest = payload[0]
    if not isinstance(newest, dict):
        return None
    tag = newest.get("tag_name")
    links = ((newest.get("assets") or {}).get("links")) or []
    wheel = next(
        (link.get("url") for link in links if str(link.get("name", "")).endswith(".whl")), None
    )
    if not tag or not wheel:
        return None
    return Release(tag=tag, wheel_url=wheel)


def _read_cache(now: float) -> Release | None:
    try:
        blob = json.loads(_cache_path().read_text())
        if now - float(blob["checked_at"]) >= CACHE_TTL:
            return None
        return Release(tag=blob["tag"], wheel_url=blob["wheel_url"])
    except (OSError, ValueError, KeyError, TypeError):
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
    except Exception:
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
    that exist here — `0.4.0` from a tag and `0.3.1.dev4+g1a2b3c` from a dev
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: PASS, every parametrised case included.

- [ ] **Step 5: Commit**

```bash
just lint
git add src/kraft/update.py tests/test_update.py
git commit -m "feat: add kraft.update, a version check that never raises

Three callers sit on paths a disconnected machine must still walk, so every
failure mode returns None instead of an exception, and a 24h disk cache keeps
a start on a slow link from paying the timeout twice."
```

---

### Task 4: `kraft admin update`

**Files:**
- Modify: `src/kraft/update.py` (add `perform`)
- Modify: `src/kraft/cli.py` (add `_cmd_update`, register in `_add_admin`)
- Modify: `tests/test_cli_verbs.py:289` (the `GROUPS` admin list)
- Test: `tests/test_update.py` (add), `tests/test_cli_admin.py` (add)

**Interfaces:**
- Consumes: `update.latest()`, `update.installed()`, `update.is_behind()`, `update.Release` from Task 3.
- Produces: `update.perform(release: Release, *, run=subprocess.run) -> int` — returns the subprocess return code. `cli._cmd_update(ns)`.

**Context:** `src/kraft/init.py:236` is the house pattern for an injectable `run=subprocess.run` — copy it rather than reaching for `unittest.mock.patch`. `src/kraft/cli.py:751` `_add_admin` is where the subparser goes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update.py`:

```python
def test_perform_installs_the_wheel_from_the_release():
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    assert seen["command"] == [
        "uv",
        "tool",
        "install",
        "--force",
        "--from",
        "https://x/w.whl",
        "kraft",
    ]


def test_perform_reports_a_failing_installer():
    def run(_command, **_kwargs):
        return type("R", (), {"returncode": 2})()

    assert update.perform(update.Release(tag="v0.4.0", wheel_url="u"), run=run) == 2


def test_perform_without_uv_is_a_readable_failure():
    def run(_command, **_kwargs):
        raise FileNotFoundError("uv")

    with pytest.raises(SystemExit, match="uv"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u"), run=run)
```

Append to `tests/test_cli_admin.py`:

```python
def test_admin_update_when_current_does_nothing(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    monkeypatch.setattr(
        update, "perform", lambda *_a, **_k: pytest.fail("installed over a current version")
    )
    cli.main(["admin", "update"])
    assert "up to date" in capsys.readouterr().out


def test_admin_update_force_installs_anyway(monkeypatch, capsys):
    from kraft import update

    called = []
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    monkeypatch.setattr(update, "perform", lambda *a, **k: called.append(a) or 0)
    cli.main(["admin", "update", "--force"])
    assert called


def test_admin_update_with_no_release_known_exits_1(monkeypatch, capsys):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["admin", "update"])
    assert exc.value.code == 1
    assert "could not reach" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_update.py -k perform tests/test_cli_admin.py -k update -v`
Expected: FAIL — `AttributeError: module 'kraft.update' has no attribute 'perform'`, and argparse rejecting `update` as an invalid choice.

- [ ] **Step 3: Add `perform`**

Append to `src/kraft/update.py`:

```python
def perform(release: Release, *, run=None) -> int:
    """Replace this install with `release`. Returns the installer's exit code.

    `uv tool install --force` is the same command `just install` ends with, so
    an updated Kraft is byte-identical to a freshly installed one rather than
    something only this path can produce.
    """
    import subprocess

    run = run or subprocess.run
    command = ["uv", "tool", "install", "--force", "--from", release.wheel_url, "kraft"]
    try:
        return run(command).returncode
    except FileNotFoundError:
        raise SystemExit(
            "kraft admin update: `uv` is not on PATH. Install it, or run this yourself:\n"
            f"  {' '.join(command)}"
        ) from None
```

- [ ] **Step 4: Add the CLI verb**

In `src/kraft/cli.py`, add a command function next to `_cmd_doctor` (around line 207):

```python
def _cmd_update(ns: argparse.Namespace) -> None:
    from kraft import update

    release = update.latest(force=True)
    if release is None:
        print(
            "kraft admin update: could not reach the release feed. Try again, "
            "or install by hand from the releases page.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    here = update.installed()
    if not update.is_behind(release) and not ns.force:
        print(f"kraft {here} is up to date ({release.tag} is the newest release)")
        return
    print(f"kraft {here} -> {release.tag}")
    code = update.perform(release)
    if code != 0:
        raise SystemExit(code)
    print(f"kraft {release.tag} installed. Restart a running server: kraft admin stop && kraft")
```

In `_add_admin` (cli.py:751), after the `doctor_p` block:

```python
    update_p = subs.add_parser("update", help="install the newest released kraft")
    update_p.add_argument(
        "--force", action="store_true", help="install even when already current"
    )
    update_p.set_defaults(func=_cmd_update)
```

- [ ] **Step 5: Teach the verb-tree test about the new verb**

`tests/test_cli_verbs.py:289` hardcodes the admin group's verbs and parametrises
over them — the tree is the interface, so a new verb must be declared there or
it is untested:

```python
    "admin": ["start", "stop", "health", "doctor", "reindex", "init", "mcp", "update"],
```

Do **not** add `update` to `cli.MOVED` or to the expected-`MOVED` set at
`tests/test_cli_verbs.py:~353`. `MOVED` records verbs that used to be top-level
and where they went; `update` never was one, and claiming otherwise would make
`kraft update` print a "moved" hint for a command that never existed.

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest tests/test_update.py tests/test_cli_admin.py tests/test_cli_verbs.py -v`
Expected: PASS.

There is deliberately no end-to-end test of `perform`: the thing it does is replace the binary running the test.

- [ ] **Step 7: Commit**

```bash
just lint
git add src/kraft/update.py src/kraft/cli.py tests/test_update.py tests/test_cli_admin.py tests/test_cli_verbs.py
git commit -m "feat: add kraft admin update

Shells out to the same `uv tool install --force` that `just install` ends
with, so an updated install is identical to a fresh one. Refuses when already
current unless --force."
```

---

### Task 5: Surface the version check on health, doctor and start

**Files:**
- Modify: `src/kraft/doctor.py` (add `_version_check`, call it)
- Modify: `src/kraft/cli.py` (`_serve`, add the boot notice)
- Test: `tests/test_cli_doctor.py`, `tests/test_cli_admin.py`

**Interfaces:**
- Consumes: `update.latest()`, `update.installed()`, `update.is_behind()`.
- Produces: `doctor._version_check() -> dict`. No new public names.

**Context:** `doctor.py:27` — `_check()`'s docstring says only a real failure may set the exit code. A version notice is not a failure. `src/kraft/cli.py:100` `_serve()` is where the boot line goes; it must print *before* `uvicorn.run`, which never returns.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_doctor.py`:

```python
def test_version_check_is_never_a_failure(monkeypatch):
    """A release day must not start failing `kraft admin doctor && deploy`."""
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v9.9.9", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.1.0")
    row = doctor._version_check()
    assert row["ok"]
    assert "9.9.9" in row["detail"] and "available" in row["detail"]


def test_version_check_says_so_when_current(monkeypatch):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    assert "newest" in doctor._version_check()["detail"]


def test_version_check_with_no_network_skips(monkeypatch):
    from kraft import update

    monkeypatch.setattr(update, "latest", lambda **_: None)
    row = doctor._version_check()
    assert row["ok"] and row["skipped"]
```

Append to `tests/test_cli_admin.py`:

```python
def test_start_prints_the_update_notice(monkeypatch, capsys):
    from kraft import cli, update

    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v9.9.9", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.1.0")
    cli._update_notice()
    assert "9.9.9" in capsys.readouterr().out


def test_start_notice_is_silenced_by_the_env_var(monkeypatch, capsys):
    from kraft import cli, update

    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(
        update, "latest", lambda **_: pytest.fail("checked with the env var set")
    )
    cli._update_notice()
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_cli_doctor.py -k version tests/test_cli_admin.py -k notice -v`
Expected: FAIL — no `_version_check`, no `_update_notice`.

- [ ] **Step 3: Add the doctor check**

In `src/kraft/doctor.py`, after `_bundle_check`:

```python
def _version_check() -> dict:
    """Informational, and `ok` even when behind.

    `doctor` exits 1 on any failed check, and a release landing must not start
    failing somebody's `kraft admin doctor && deploy`. Being out of date is a
    thing to know, not a thing that is broken.
    """
    from kraft import update

    release = update.latest()
    here = update.installed()
    if release is None:
        return _check("version", True, f"{here} (skipped: no release feed)", skipped=True)
    if update.is_behind(release):
        return _check("version", True, f"{here} installed, {release.tag} available")
    return _check("version", True, f"{here} (the newest release)")
```

Append it in `run_checks()` after `_bundle_check()`:

```python
    checks.append(_bundle_check())
    checks.append(_version_check())
```

- [ ] **Step 4: Add the boot notice**

In `src/kraft/cli.py`, add above `_serve`:

```python
def _update_notice() -> None:
    """One line at boot when a newer release exists.

    Reads the 24h cache, so an ordinary start pays nothing. A cold cache on a
    machine with no route out costs `update.TIMEOUT` once a day, and
    `KRAFT_NO_UPDATE_CHECK=1` costs nothing ever.
    """
    if os.environ.get("KRAFT_NO_UPDATE_CHECK"):
        return
    from kraft import update

    release = update.latest()
    if update.is_behind(release):
        print(f"kraft: {update.installed()} installed, {release.tag} available - kraft admin update")
```

Call it in `_serve()`, immediately before the `print(f"kraft: http://{host}:{port}")` line (cli.py:118) — after the pidfile is written, so a start that refuses for a stale pid does not first advertise an upgrade.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_cli_doctor.py tests/test_cli_admin.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
just lint
git add src/kraft/doctor.py src/kraft/cli.py tests/test_cli_doctor.py tests/test_cli_admin.py
git commit -m "feat: report a newer release on doctor, health and start

Informational only: the doctor row is ok even when behind, so a release day
does not start failing `kraft admin doctor && deploy`. KRAFT_NO_UPDATE_CHECK=1
skips the check for a machine that is deliberately offline."
```

---

### Task 6: The agent skills become files

**Files:**
- Create: `src/kraft/plugin/skills/handoff/SKILL.md`, `src/kraft/plugin/skills/board/SKILL.md`, `src/kraft/plugin/skills/gates/SKILL.md`
- Modify: `src/kraft/init.py` (delete `SKILLS`, read the directory instead)
- Modify: `pyproject.toml` (`package-data`)
- Test: `tests/test_init.py` (verify the existing tests still pass unchanged)

**Interfaces:**
- Consumes: nothing.
- Produces: `init.SKILLS_DIR: Path`. `init._write_plugin(root)` keeps its exact signature and return value.

**Context:** `src/kraft/init.py:24` `SKILLS` is a dict of name to SKILL.md body. `_write_plugin` (init.py:190) iterates it. This task changes where the bodies come from and nothing else — **the written result must be byte-identical**, which is what the existing `admin init` tests check.

- [ ] **Step 1: Confirm the tests that must not change**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS. Note the count. These same tests passing unchanged at Step 5 is this task's real assertion — a change in them means the skill content drifted during the move.

- [ ] **Step 2: Move each body to a file**

For each of `handoff`, `board`, `gates`, copy the string literal's contents from `src/kraft/init.py` into `src/kraft/plugin/skills/<name>/SKILL.md`, verbatim including the `---` frontmatter.

Verify byte-for-byte before deleting anything:

```bash
uv run python - <<'EOF'
from pathlib import Path
from kraft import init
for name, body in init.SKILLS.items():
    on_disk = Path(f"src/kraft/plugin/skills/{name}/SKILL.md").read_text()
    assert on_disk == body, f"{name} differs"
print("all three identical")
EOF
```

Expected: `all three identical`. Do not proceed until it prints that.

- [ ] **Step 3: Read the directory instead of the dict**

In `src/kraft/init.py`, delete the whole `SKILLS = {...}` literal and replace it with:

```python
#: One skill per moment you would reach for Kraft, rather than one skill listing
#: every tool. The manifest in `_plugin_manifest` turns this directory into a
#: namespace, so these are invoked as `/kraft:handoff`, `/kraft:board`,
#: `/kraft:gates`.
#:
#: Files rather than string literals because this same directory is published as
#: a marketplace plugin, and the same content maintained in two places is the
#: same content that starts disagreeing.
SKILLS_DIR = Path(__file__).parent / "plugin" / "skills"
```

In `_write_plugin` (init.py:190), replace the `for name, body in SKILLS.items():` loop with:

```python
    for source in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        path = plugin_root / "skills" / source.parent.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.read_text())
        written.append(str(path))
```

`Path` is already imported at init.py:19.

- [ ] **Step 4: Ship the files in the wheel**

In `pyproject.toml`:

```toml
[tool.setuptools.package-data]
kraft = ["_bundled/**/*", "skills/**/*", "plugin/**/*"]
```

Without this, `admin init` from an installed Kraft finds an empty `SKILLS_DIR` and writes a plugin with no skills.

- [ ] **Step 5: Run the unchanged tests**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS, same count as Step 1, with no edits to the test file.

- [ ] **Step 6: Verify it survives being installed**

```bash
just install && kraft admin init --repo && cat .claude/skills/kraft/skills/board/SKILL.md
```

Expected: the board skill's content. Then `git checkout .mcp.json 2>/dev/null; rm -rf .claude/skills/kraft` to clean up.

- [ ] **Step 7: Commit**

```bash
just lint
git add src/kraft/init.py src/kraft/plugin pyproject.toml
git commit -m "refactor: the agent skills become files, not string literals

Same content, same written result — the admin init tests pass unchanged,
which is the point. A marketplace plugin needs these as files, and the same
four skills maintained in two places would start disagreeing."
```

---

### Task 7: The marketplace plugin

**Files:**
- Create: `src/kraft/plugin/.claude-plugin/plugin.json`, `src/kraft/plugin/.claude-plugin/marketplace.json`, `src/kraft/plugin/README.md`, `src/kraft/plugin/LICENSE`
- Modify: each `SKILL.md` from Task 6 (the missing-binary line)
- Modify: `justfile` (add `plugin-publish`)
- Rename: `dev/check_lite_version.py` -> `dev/check_plugin_version.py`, `tests/test_check_lite_version.py` -> `tests/test_check_plugin_version.py`
- Modify: `.gitlab-ci.yml` (`lite-version` job's script, add a `plugin-version` job)

**Interfaces:**
- Consumes: `src/kraft/plugin/skills/` from Task 6.
- Produces: `dev/check_plugin_version.py` with signature `main(manifest: str, base: str, head: str, *surface: str)`.

**Context:** `justfile` `lite-publish` is the model to copy — read it in full first. `dev/check_lite_version.py:24` defines `SURFACE`; that constant becomes an argument.

- [ ] **Step 1: Add the missing-binary line to each skill**

At the top of the body (after the frontmatter) of all three `SKILL.md` files:

```markdown
Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://gitlab.com/itsOmidKarami/kraft#install rather than reporting a
connection error.
```

- [ ] **Step 2: Verify the skills still write correctly**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS. (These tests assert structure, not exact bodies — if one asserts an exact body, update that assertion, since the content change is intended here and was not in Task 6.)

- [ ] **Step 3: Write the manifests**

`src/kraft/plugin/.claude-plugin/plugin.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "kraft",
  "version": "0.0.0",
  "description": "Drive Kraft from an agent session: file work, read the board, act on gates.",
  "author": {
    "name": "Omid Karami",
    "url": "https://github.com/itsOmidKarami"
  },
  "skills": ["./skills"]
}
```

`src/kraft/plugin/.claude-plugin/marketplace.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "kraft",
  "description": "Drive Kraft from an agent session: file work, read the board, act on gates.",
  "owner": {
    "name": "Omid Karami",
    "url": "https://github.com/itsOmidKarami"
  },
  "plugins": [
    {
      "name": "kraft",
      "description": "Drive Kraft from an agent session: file work, read the board, act on gates.",
      "source": "./",
      "category": "productivity"
    }
  ]
}
```

The `"0.0.0"` is a placeholder stamped by `just plugin-publish` from the git tag. A static manifest cannot compute what `importlib.metadata` computes, so it is written at publish time.

`src/kraft/plugin/README.md` states in three lines what the plugin is, that it requires the `kraft` program, and links the install instructions. Copy `plugins/kraft-lite/LICENSE` to `src/kraft/plugin/LICENSE`.

- [ ] **Step 4: Generalise the version check — write the failing test first**

```bash
git mv dev/check_lite_version.py dev/check_plugin_version.py
git mv tests/test_check_lite_version.py tests/test_check_plugin_version.py
```

In `tests/test_check_plugin_version.py`, update the import, then add:

`dev/` has no `__init__.py`, and `tests/test_check_lite_version.py` invokes the
script by path (`SCRIPT = REPO / "dev" / "check_lite_version.py"`) rather than
importing it. Keep that: update `SCRIPT` to the new filename and pass the
manifest and surface as argv, exactly as CI will.

```python
def test_the_manifest_and_surface_come_from_argv(tmp_path):
    """One checker, two plugins. A second copy of this logic is the thing to avoid."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "manifest" in (result.stderr + result.stdout).lower()
```

Replace every hard-coded `plugins/kraft-lite/...` in the existing tests with a path built from the fixture, so the tests exercise the argument rather than the old constant.

- [ ] **Step 5: Run it to see it fail**

Run: `uv run pytest tests/test_check_plugin_version.py -v`
Expected: FAIL — the module still reads its module-level `MANIFEST` and `SURFACE` constants.

- [ ] **Step 6: Make the checker take arguments**

In `dev/check_plugin_version.py`, delete the `MANIFEST` and `SURFACE` constants and give `main` the signature `main(manifest: str, base: str, head: str, *surface: str)`, reading them from `sys.argv` in the `__main__` block. Update the module docstring: it now serves both plugins, and the usage line becomes

```
python3 dev/check_plugin_version.py <manifest> <base-ref> <head-ref> <surface-path>...
```

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_check_plugin_version.py -v`
Expected: PASS.

- [ ] **Step 8: Point CI at both plugins**

In `.gitlab-ci.yml`, change `lite-version`'s script line to:

```yaml
    - >
      python3 dev/check_plugin_version.py
      plugins/kraft-lite/.claude-plugin/plugin.json FETCH_HEAD "$CI_COMMIT_SHA"
      plugins/kraft-lite/kl.py plugins/kraft-lite/skills
      plugins/kraft-lite/chains plugins/kraft-lite/.claude-plugin
```

The kraft plugin needs no equivalent job: its version is the git tag, and Task 9's label is what advances it. Add that as a comment where a `plugin-version` job would otherwise go, so the asymmetry reads as a decision rather than an omission.

- [ ] **Step 9: Add the publish recipe**

Add to `justfile`. `lite-publish` is the model — read it first — but this one
cannot use `git subtree split`: the published `plugin.json` carries a version
stamped from the git tag, and a split publishes what is committed. It builds an
orphan history from a stamped copy instead, which the force-push arrangement
already permits.

```
# Publish src/kraft/plugin/ to its own public repo; regenerates and force-pushes.
# Same one-way arrangement as lite-publish, and the same expiry: the day that
# repo has an external contributor, force-pushing destroys their merge base --
# stop running this and make the public repo the source instead.
#
# Not a subtree split, unlike lite-publish. The version in the published
# plugin.json is stamped from the git tag rather than committed here, because a
# static manifest cannot compute what importlib.metadata computes, and a split
# publishes only what is already committed.
#
# Prerequisite: git remote add plugin git@github.com:itsOmidKarami/kraft-plugin.git
plugin-publish:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(git describe --tags --abbrev=0 | sed 's/^v//')
    test -n "$version"
    uv run pytest tests/test_init.py -q
    test -f src/kraft/plugin/LICENSE
    # Same exit-code reading as lite-publish: 0 means this version is already
    # out, 2 means it is not, and anything else is a remote that could not be
    # reached -- which must not read as a clean publish.
    tag="kraft--v$version"
    rc=0; git ls-remote --exit-code --tags plugin "$tag" >/dev/null || rc=$?
    case $rc in
        0) echo "kraft plugin v$version is already published -- tag a new release first"; exit 1 ;;
        2) ;;
        *) echo "cannot reach the plugin remote: git ls-remote exited $rc"; exit 1 ;;
    esac
    remote=$(git remote get-url plugin)
    tmp=$(mktemp -d)
    cp -R src/kraft/plugin/. "$tmp"
    python3 - "$tmp/.claude-plugin/plugin.json" "$version" <<'EOF'
    import json, sys
    path, version = sys.argv[1], sys.argv[2]
    manifest = json.loads(open(path).read())
    manifest["version"] = version
    open(path, "w").write(json.dumps(manifest, indent=2) + "\n")
    EOF
    claude plugin validate "$tmp" --strict
    git -C "$tmp" init -q
    git -C "$tmp" add -A
    git -C "$tmp" commit -qm "kraft plugin v$version"
    git -C "$tmp" tag "$tag"
    # Atomic: a tag push that fails after main moved would leave the release
    # untagged, and the next force-push makes that commit unreachable.
    git -C "$tmp" push --force --atomic "$remote" HEAD:main "$tag"
    rm -rf "$tmp"
    echo "published kraft plugin v$version"
```

- [ ] **Step 10: Verify the plugin validates**

Run: `claude plugin validate src/kraft/plugin --strict`
Expected: no errors. (The `"0.0.0"` placeholder validates fine; it is a well-formed version.)

- [ ] **Step 11: Commit**

```bash
just lint
uv run pytest tests/test_init.py tests/test_check_plugin_version.py -q
git add -A src/kraft/plugin dev justfile .gitlab-ci.yml tests
git commit -m "feat: publish the kraft skills as a marketplace plugin

The same directory admin init writes from is now also a publishable plugin
tree. check_lite_version generalises to check_plugin_version rather than
gaining a twin."
```

---

### Task 8: The tag pipeline and `install.sh`

**Files:**
- Create: `install.sh`
- Modify: `.gitlab-ci.yml` (add a `release` stage and job)
- Modify: `README.md` (the install section)

**Interfaces:**
- Consumes: `just bundle` (Task 2), the setuptools-scm version (Task 1).
- Produces: a GitLab Release per tag with a `.whl` asset link, which `update.latest()` (Task 3) reads and `install.sh` fetches.

**Context:** `.gitlab-ci.yml:4` `workflow.rules` already admits `$CI_COMMIT_TAG` — no change needed there. The `frontend-e2e` job is the model for a job that needs both node and uv.

- [ ] **Step 1: Add the release job**

Add to `.gitlab-ci.yml`:

```yaml
stages: [test, release]

# A tag is the only thing that publishes. The workflow rules above already admit
# tag pipelines, so this job's rule is the whole gate.
release:
  stage: release
  rules:
    - if: $CI_COMMIT_TAG
  variables:
    # setuptools-scm reads the version from tags. Without this the wheel would
    # be versioned 0.1.dev1 no matter what tag triggered the pipeline.
    GIT_DEPTH: 0
  before_script:
    - apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl nodejs npm
    - curl -LsSf https://astral.sh/uv/install.sh | sh
    - export PATH="$HOME/.local/bin:$PATH"
    - curl -fsSL "https://github.com/casey/just/releases/latest/download/just-x86_64-unknown-linux-musl.tar.gz" | tar -xz -C /usr/local/bin just
  script:
    - export PATH="$HOME/.local/bin:$PATH"
    - cd frontend && npm ci && cd ..
    - just bundle
    - uv build --wheel
    - WHEEL=$(ls dist/*.whl)
    # Smoke test: a wheel that lost the SPA, or that versioned itself from a
    # shallow clone, must not become a release. This is the guard that makes
    # `just bundle` living outside the build backend survivable.
    - uv venv /tmp/smoke
    - VIRTUAL_ENV=/tmp/smoke uv pip install "$WHEEL"
    - /tmp/smoke/bin/kraft --version | grep -q "${CI_COMMIT_TAG#v}"
    # Ask the installed package where its bundle is, rather than guessing a
    # site-packages path that a python version bump would silently invalidate.
    - >
      /tmp/smoke/bin/python -c
      "from kraft.paths import BUNDLED; import sys;
      sys.exit(0 if (BUNDLED / 'web' / 'index.html').is_file() else 1)"
    - |
      curl --fail --header "JOB-TOKEN: $CI_JOB_TOKEN" --upload-file "$WHEEL" \
        "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/generic/kraft/${CI_COMMIT_TAG#v}/$(basename "$WHEEL")"
    - echo "WHEEL_URL=${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/generic/kraft/${CI_COMMIT_TAG#v}/$(basename "$WHEEL")" >> release.env
  artifacts:
    paths: [dist/*.whl]
    reports:
      dotenv: release.env
  release:
    tag_name: $CI_COMMIT_TAG
    description: "Kraft $CI_COMMIT_TAG"
    assets:
      links:
        - name: "kraft-${CI_COMMIT_TAG}-py3-none-any.whl"
          url: "$WHEEL_URL"
```

The asset link's `name` must end in `.whl` — `update._parse` (Task 3) selects the wheel by that suffix, and a link named otherwise makes every release look uninstallable.

Add `stage: test` to the existing `lint-and-test`, `slow-tests`, `lite-version`, `lite-floor`, `frontend` and `frontend-e2e` jobs, since declaring `stages` makes the default explicit rather than implied.

- [ ] **Step 2: Write `install.sh`**

Create `install.sh` at the repo root, `chmod +x`:

```sh
#!/bin/sh
# Install the newest released Kraft.
#
#   curl -fsSL https://gitlab.com/itsOmidKarami/kraft/-/raw/main/install.sh | sh
#
# This is a piped-shell install, with the objections that implies. What reduces
# them: this script is short, committed, and reviewable at the URL above before
# you run it, and the wheel it fetches is an asset of a tagged release rather
# than something from a mutable location. What does not reduce them is arguing
# the pattern is fine.
set -eu

API="https://gitlab.com/api/v4/projects/itsOmidKarami%2Fkraft/releases"

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv (kraft needs it to fetch a Python 3.14)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

wheel=$(curl -fsSL "$API" | grep -o 'https://[^"]*\.whl' | head -n 1)
if [ -z "$wheel" ]; then
    echo "no released wheel found at $API" >&2
    exit 1
fi

uv tool install --force --from "$wheel" kraft

echo
echo "kraft $(kraft --version) installed."
echo "next: kraft admin init   # register Kraft with your agent"
echo "then: kraft              # start the server"
```

- [ ] **Step 3: Check the script for syntax and shell portability**

Run: `sh -n install.sh && shellcheck install.sh 2>/dev/null || true`
Expected: no syntax errors. `shellcheck` may not be installed; the `sh -n` parse is the part that must pass.

- [ ] **Step 4: Rewrite the README install section**

Replace the clone-and-`just install` instructions with the `curl | sh` one-liner, and keep `just install` below it under a "From source (development)" heading. Say plainly that the one-liner needs no clone.

- [ ] **Step 5: Commit**

```bash
just lint
git add .gitlab-ci.yml install.sh README.md
git commit -m "feat: publish a wheel and a release on every tag

The release job smoke-installs the wheel it built and asserts both the version
and the SPA before the release exists, so a wheel that lost either never
reaches anybody. install.sh makes installing one line and no clone."
```

---

### Task 9: `release::` labels, and the tag that follows a merge

**Files:**
- Create: `dev/next_tag.py`
- Create: `tests/test_next_tag.py`
- Modify: `.gitlab-ci.yml` (two jobs)
- Modify: `CONTRIBUTING.md` (create if absent)

**Interfaces:**
- Consumes: the release job from Task 8, which this triggers.
- Produces: `next_tag(previous: str | None, impact: str) -> str | None` and `impact_from_labels(labels: str) -> str | None` in `dev/next_tag.py`.

**Context:** This is the piece that keeps the version moving. Deriving the version from a tag removed the bump commit, and with it the thing anyone could forget *loudly* — see the spec's section 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_next_tag.py`:

```python
"""The tag a merge produces.

Pure functions, because everything else in this mechanism is a CI job that
cannot be run here. This is the part that can be wrong quietly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# `dev/` is not a package (no __init__.py), and tests/test_check_plugin_version.py
# invokes its script by path. This one needs the functions rather than the exit
# code, so it loads the file directly instead of adding a package just for tests.
_SPEC = importlib.util.spec_from_file_location(
    "next_tag", Path(__file__).resolve().parents[1] / "dev" / "next_tag.py"
)
next_tag_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["next_tag"] = next_tag_mod
_SPEC.loader.exec_module(next_tag_mod)

impact_from_labels = next_tag_mod.impact_from_labels
next_tag = next_tag_mod.next_tag


@pytest.mark.parametrize(
    ("previous", "impact", "expected"),
    [
        ("v0.3.2", "patch", "v0.3.3"),
        ("v0.3.2", "minor", "v0.4.0"),
        ("v0.3.2", "major", "v1.0.0"),
        ("v1.9.9", "major", "v2.0.0"),
        (None, "patch", "v0.0.1"),
        (None, "minor", "v0.1.0"),
        (None, "major", "v1.0.0"),
        ("v0.3.2", "none", None),
    ],
)
def test_next_tag(previous, impact, expected):
    assert next_tag(previous, impact) == expected


def test_a_tag_without_three_components_is_refused():
    """Better to fail the job than to guess and tag something wrong."""
    with pytest.raises(ValueError):
        next_tag("v0.3", "patch")


def test_an_unknown_impact_is_refused():
    with pytest.raises(ValueError):
        next_tag("v0.3.2", "enormous")


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ("release::minor", "minor"),
        ("bug,release::patch,frontend", "patch"),
        ("release::none", "none"),
        ("bug,frontend", None),
        ("", None),
    ],
)
def test_impact_from_labels(labels, expected):
    assert impact_from_labels(labels) == expected
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_next_tag.py -v`
Expected: FAIL at collection, `FileNotFoundError` on `dev/next_tag.py`.

- [ ] **Step 3: Write the module**

Create `dev/next_tag.py`:

```python
"""Compute the tag a merge should produce, from the merge request's label.

The version comes from the git tag, so "bumping the version" is "making a tag" —
and a tag nobody makes fails silently forever. Every merge request declares its
release impact, and merging is what tags.

Usage: python3 dev/next_tag.py <previous-tag-or-empty> <labels>
Prints the tag to create, or nothing when the impact is `none`.
"""

from __future__ import annotations

import sys

PREFIX = "release::"
IMPACTS = ("major", "minor", "patch", "none")


def impact_from_labels(labels: str) -> str | None:
    """The declared impact, or `None` when the MR did not declare one.

    GitLab's scoped labels guarantee at most one `release::` label per MR, so
    the first match is the only match.
    """
    for label in labels.split(","):
        name = label.strip()
        if name.startswith(PREFIX) and name[len(PREFIX) :] in IMPACTS:
            return name[len(PREFIX) :]
    return None


def next_tag(previous: str | None, impact: str) -> str | None:
    """The next tag, or `None` for `none`.

    A malformed previous tag raises rather than guessing: tagging the wrong
    version is worse than a failed job somebody has to look at.
    """
    if impact not in IMPACTS:
        raise ValueError(f"unknown release impact {impact!r}; expected one of {IMPACTS}")
    if impact == "none":
        return None
    major, minor, patch = (0, 0, 0)
    if previous:
        parts = previous.lstrip("v").split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"cannot read a version out of {previous!r}")
        major, minor, patch = (int(p) for p in parts)
        if impact == "major":
            major, minor, patch = major + 1, 0, 0
        elif impact == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
    else:
        # No tag in the history: this is the first release, and the impact
        # names which component the project starts at.
        major, minor, patch = {"major": (1, 0, 0), "minor": (0, 1, 0), "patch": (0, 0, 1)}[impact]
    return f"v{major}.{minor}.{patch}"


if __name__ == "__main__":
    previous, labels = sys.argv[1] or None, sys.argv[2]
    impact = impact_from_labels(labels)
    if impact is None:
        raise SystemExit(f"no {PREFIX} label; expected one of {IMPACTS}")
    tag = next_tag(previous, impact)
    if tag:
        print(tag)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_next_tag.py -v`
Expected: PASS, all parametrised cases.

- [ ] **Step 5: Add the two CI jobs**

```yaml
# Every merge request declares what it ships. The version comes from the git tag
# now, so there is no bump commit left to forget -- which means the only place
# anyone can be asked is here, while the author still knows what the change is
# worth. `release::none` is a first-class answer and the expected one for docs,
# comments, CI config and test-only changes.
release-impact:
  stage: test
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - >
      echo "$CI_MERGE_REQUEST_LABELS" | grep -q 'release::' ||
      { echo "This MR needs one of: release::major, release::minor, release::patch, release::none"; exit 1; }
    - python3 dev/next_tag.py "" "$CI_MERGE_REQUEST_LABELS" > /dev/null || true
    - echo "declared:$CI_MERGE_REQUEST_LABELS"

# The merge landed. Read the label off the MR that produced this commit and tag,
# which fires the `release` job above via a fresh tag pipeline.
#
# This job must never run on a tag pipeline, or the tag it pushes triggers it
# again forever. The workflow rules separate $CI_COMMIT_TAG from
# $CI_COMMIT_BRANCH, and the rule below is branch-only, so that holds -- but it
# is the failure worth naming rather than trusting.
auto-tag:
  stage: release
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  variables:
    GIT_DEPTH: 0
  before_script:
    - apt-get update && apt-get install -y --no-install-recommends git curl jq ca-certificates
  script:
    - |
      LABELS=$(curl -fsSL --header "PRIVATE-TOKEN: $RELEASE_TOKEN" \
        "$CI_API_V4_URL/projects/$CI_PROJECT_ID/repository/commits/$CI_COMMIT_SHA/merge_requests" \
        | jq -r '.[0].labels // [] | join(",")')
    # A commit that reached main without a merge request -- a direct push, the
    # revert button, a cherry-pick -- has no labels to read. That is `none`, and
    # saying so is what stops it looking like a broken job.
    - if [ -z "$LABELS" ]; then echo "no merge request for $CI_COMMIT_SHA; not releasing"; exit 0; fi
    - PREVIOUS=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    - TAG=$(python3 dev/next_tag.py "$PREVIOUS" "$LABELS" || true)
    - if [ -z "$TAG" ]; then echo "release::none; not releasing"; exit 0; fi
    - git remote set-url origin "https://oauth2:${RELEASE_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git"
    - git tag -a "$TAG" -m "$TAG"
    - git push origin "$TAG"
    - echo "tagged $TAG"
```

- [ ] **Step 6: Document the process and its prerequisites**

Create or extend `CONTRIBUTING.md` with a "Releasing" section: the four labels, what each means, that `release::none` is expected for docs and CI changes, and that merging is what releases. List the two manual prerequisites explicitly (they are in Task 10's checklist too, and both places are load-bearing — one is read by a maintainer, the other by whoever sets this up).

- [ ] **Step 7: Commit**

```bash
just lint
uv run pytest tests/test_next_tag.py -q
git add dev/next_tag.py tests/test_next_tag.py .gitlab-ci.yml CONTRIBUTING.md
git commit -m "feat: declare release impact per MR, and tag on merge

Deriving the version from the tag removed the bump commit, and with it the
thing anyone could forget loudly. The label is asked for at the only moment
the author knows what the change is worth, and merging is what tags."
```

---

### Task 10: The manual prerequisites, written down

**Files:**
- Modify: `README.md` or `CONTRIBUTING.md` (a "Release setup" section)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing executable. This task exists because two steps in this design cannot be code, and an undocumented manual step is a design that stops working the first time somebody new touches it.

- [ ] **Step 1: Write the checklist**

```markdown
## Release setup (once, by a maintainer)

Neither of these can be done by code in this repo. Until both exist, the
`release-impact` job fails every merge request and `auto-tag` cannot push.

1. **Create four scoped labels** in the GitLab project: `release::major`,
   `release::minor`, `release::patch`, `release::none`. The `::` matters —
   scoped labels are what guarantee an MR carries at most one of them.
2. **Create a project access token** with the `api` scope and the Maintainer
   role, and add it as a masked, protected CI/CD variable named
   `RELEASE_TOKEN`. `CI_JOB_TOKEN` cannot push tags, which is why this is the
   one new secret the release mechanism needs.
3. **Protect the `v*` tag pattern** with "Maintainers" allowed to create, so
   nothing but this token and a human maintainer can publish a release.
4. **Add the plugin remote** for `just plugin-publish`:
   `git remote add plugin git@github.com:itsOmidKarami/kraft-plugin.git`

The merge request that introduces this mechanism needs a `release::` label
itself, so create the labels before opening it.
```

- [ ] **Step 2: Commit**

```bash
git add README.md CONTRIBUTING.md
git commit -m "docs: the manual prerequisites for releasing

Two of the steps in the release mechanism cannot be code. An undocumented
manual step is a design that stops working the first time somebody new
touches it."
```

---

## Verification

After every task, before handing the branch on:

```bash
just lint
uv run pytest tests/test_version.py tests/test_update.py tests/test_cli_admin.py \
  tests/test_cli_doctor.py tests/test_init.py tests/test_next_tag.py \
  tests/test_check_plugin_version.py -q
```

The full `just test` takes ~14 minutes and is CI's job, not this branch's.

The one thing no test here covers is the pipeline itself. The first tag is its
test, and the release job's smoke install is what makes a failure loud rather
than a wheel with no UI sitting on a releases page.
