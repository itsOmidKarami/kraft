"""Reading and writing the YAML files the Settings screens edit.

`02` §4.7 (revised): the UI is an editor for files git tracks, not a front end
for a config table. Every write here lands in the same `templates/` directory an
operator edits by hand, so a change stays reviewable, diffable and revertible.

Writes are atomic — a temp file in the same directory, then `os.replace` — so a
crash mid-save can never leave a half-written policy the next start refuses to
load.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from pathlib import Path

import yaml

from kraft import steering as _steering

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


def read_yaml(path: str | Path, default: dict | None = None) -> dict:
    """Parse a config file. A missing file reads as `default`, a broken one raises."""
    path = Path(path)
    if not path.exists():
        return dict(default or {})
    try:
        data = yaml.safe_load(path.read_text())
    # ValueError covers the UnicodeDecodeError `read_text()` raises on a file
    # with invalid bytes: not an OSError, and not a yaml.YAMLError, so without
    # it a broken config crashes startup instead of being reported as broken.
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path.name}: cannot read/parse: {exc}") from exc
    if data is None:
        return dict(default or {})
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a mapping at the top level")
    return data


def write_text(path: str | Path, text: str) -> None:
    """Write `text`, atomically. A reader sees the old file or the new one.

    Every config Kraft owns is read straight off disk by something that did not
    write it -- steering bodies at agent dispatch, the rest at load -- so a
    half-written file is a half-configured launch, not a cosmetic problem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_yaml(path: str | Path, data: dict) -> None:
    write_text(path, yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


# ── repos ────────────────────────────────────────────────────────────────────

REPOS_DEFAULT: dict = {"repos": []}


def _normalize_forge(repo: dict) -> None:
    """Read a pre-forge repos.yaml entry in place.

    `templates/` is seeded once and never overwritten, so compatibility for the
    `gitlab_project` -> `forge`/`project` rename lives in the reader rather than
    in a migration that would rewrite a file the user owns.
    """
    legacy = repo.pop("gitlab_project", None)
    # Both `forge` and `project` must be absent: a hand-edited half-migrated
    # entry carrying an explicit `project` beside the legacy key keeps its own.
    if repo.get("forge") is None and repo.get("project") is None and legacy:
        repo["forge"] = "gitlab"
        repo["project"] = legacy
    repo.setdefault("forge", None)
    repo.setdefault("project", None)


def load_repos(
    path: str | Path, *, steering_dir: Path | None = None, validate_steering: bool = True
) -> list[dict]:
    """Parse `repos.yaml`, or raise `ConfigError`.

    `validate_steering` defaults on for direct/library callers, but the API's
    read routes (`GET /repos`, `PATCH`/`DELETE /repos`, template validation)
    pass it off: a steering file deleted after the fact must not 422 the very
    screens an operator would use to fix it (the only escape otherwise is
    hand-editing YAML — there is no Settings screen for steering files). The
    write path stays strict: `_validate_repos` loads the candidate with
    `validate_steering=True` (the default) before it is ever saved, and
    `_launch`/`run_agent_task` already tolerate a steering name whose file is
    gone by the time it is actually read.
    """
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
    data = read_yaml(path, REPOS_DEFAULT)
    repos = data.get("repos") or []
    if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
        raise ConfigError("repos.yaml: 'repos' must be a list of mappings")
    for r in repos:
        if not isinstance(r.get("path"), str) or not r["path"]:
            raise ConfigError("repos.yaml: every repo needs a string 'path'")
        _normalize_forge(r)
        r.setdefault("default_model", None)
        if r.get("default_model") is not None and not isinstance(r["default_model"], str):
            raise ConfigError("repos.yaml: 'default_model' must be a string")
        for key in ("deny_tools", "steering"):
            v = r.setdefault(key, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"repos.yaml: {key!r} must be a list of strings")
        if validate_steering:
            try:
                _steering.validate(steering_dir, r.get("steering", []), where="repos.yaml")
            except _steering.SteeringError as exc:
                raise ConfigError(str(exc)) from exc
    return repos


def save_repos(path: str | Path, repos: list[dict]) -> None:
    write_yaml(path, {"repos": repos})


def git_read(
    cwd: Path, *args: str, expected_failure: bool = False, strip: bool = True
) -> str | None:
    """One read-only git command, or None if git says no. Never raises.

    `--no-optional-locks`: a plain `git status`/`diff` still touches
    `.git/index`'s mtime (refreshing stat data), which collides with an
    `index.lock` an agent is holding mid-run. This flag skips that refresh;
    content reads are unaffected, only the lock-taking side effect is.

    `expected_failure` drops the non-zero exit to debug for lookups whose
    failure the caller already handles — `remote get-url origin` on a repo with
    no origin is a normal probe outcome, not a fault, and warning on it puts a
    line in the log (and in test output) for every origin-less repo. The
    warning itself stays: it exists so a broken worktree cannot produce a 500
    whose cause is recorded nowhere.

    `strip=False` for the callers that read *content* rather than a scalar: a
    diff body ending in a blank context line loses that line to the strip, and
    git_read is a diff transport now as well as a `rev-parse` reader.
    """
    cmd = ["git", "--no-optional-locks", *args]
    try:
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git_read could not run %s in %s: %s", cmd, cwd, exc)
        return None
    if out.returncode != 0:
        log = logger.debug if expected_failure else logger.warning
        log("git_read %s in %s failed: %s", cmd, cwd, out.stderr.strip())
        return None
    return out.stdout.strip() if strip else out.stdout


#: Test commands to look for, in the order a repo is most likely to want them.
_TEST_COMMANDS = [
    ("pyproject.toml", "uv run pytest -q"),
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
]


#: Hostnames Kraft can recognize in an origin URL. Self-hosted instances have
#: arbitrary hostnames and no read-only signal, so they are set by hand in
#: repos.yaml instead.
_FORGES = {
    "gitlab.com": "gitlab",
    "github.com": "github",
}


def _detect_forge(remote: str) -> tuple[str | None, str | None]:
    """(forge, project) from an origin URL, or (None, None). Never raises."""
    for host, forge in _FORGES.items():
        if host in remote:
            tail = remote.split(host, 1)[-1].lstrip(":/")
            return forge, (tail.removesuffix(".git") or None)
    return None, None


def probe_repo(path: str | Path) -> dict:
    """What Kraft can tell about a candidate repo without changing anything.

    Read-only on purpose (design 5a): "Kraft flags it but does not change repo
    files". Everything is best-effort — a field it cannot determine comes back
    None or empty rather than failing the probe.
    """
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ConfigError(f"{p} is not a directory")
    toplevel = git_read(p, "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        raise ConfigError(f"{p} is not a git repository")
    root = Path(toplevel)

    submodules: list[str] = []
    gitmodules = root / ".gitmodules"
    if gitmodules.is_file():
        parser = ConfigParser()
        try:
            # .gitmodules is INI-shaped: [submodule "libs/x"] with a path key.
            # ValueError covers read_text()'s UnicodeDecodeError: this read is
            # best-effort, so a .gitmodules that is not UTF-8 degrades to no
            # submodules rather than failing the whole probe.
            parser.read_string(gitmodules.read_text())
            submodules = sorted(
                parser.get(s, "path") for s in parser.sections() if parser.has_option(s, "path")
            )
        except ConfigParserError, OSError, ValueError:
            submodules = []

    beads = root / ".beads"
    beads_config = read_yaml(beads / "config.yaml", {}) if beads.is_dir() else {}
    export = beads_config.get("export") or {}

    remote = git_read(root, "remote", "get-url", "origin", expected_failure=True) or ""
    forge, project = _detect_forge(remote)

    test_command = next((cmd for marker, cmd in _TEST_COMMANDS if (root / marker).is_file()), None)

    return {
        "path": str(root),
        "name": root.name,
        "branch": git_read(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "submodules": submodules,
        "has_beads": beads.is_dir(),
        "beads_export_auto": bool(export.get("auto")),
        "beads_export_git_add": bool(export.get("git-add")),
        "has_engineering": (root / ".engineering").is_dir(),
        "test_command": test_command,
        "forge": forge,
        "project": project,
    }


# ── access ───────────────────────────────────────────────────────────────────

ACCESS_DEFAULT: dict = {
    "bind": "127.0.0.1",
    "port": 8765,
    "password_hash": None,
    "session_expiry_days": 7,
}

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def load_access(path: str | Path) -> dict:
    return {**ACCESS_DEFAULT, **read_yaml(path, ACCESS_DEFAULT)}


def save_access(path: str | Path, access: dict) -> None:
    write_yaml(path, {k: access.get(k, v) for k, v in ACCESS_DEFAULT.items()})


# ── notify ───────────────────────────────────────────────────────────────────

#: `templates/notify.yaml`. Not bundled and not seeded, for `access.yaml`'s
#: reason: it holds a secret and a hostname that belong to one machine. A
#: missing file reads as this, and the first `PUT /notify` creates it.
NOTIFY_DEFAULT: dict = {
    "enabled": False,
    "url": None,
    "base_url": None,
    "events": ["gate_requested", "work_item_needs_human"],
}


def load_notify(path: str | Path) -> dict:
    try:
        overrides = read_yaml(path, {})
    except ConfigError as exc:
        # notify.yaml holds a webhook URL. read_yaml's ConfigError message embeds
        # the underlying exception, which for a YAML/decode error quotes the
        # offending source line verbatim -- for this file that line is the
        # secret. Every other config file wants that parser detail back; only
        # this one gets a sanitized message instead, and `from None` drops the
        # chained original (and its embedded token) out of any traceback.
        #
        # An OSError (permission denied, e.g.) carries no file content -- its
        # `strerror` is a libc message -- so it is safe to surface, and doing
        # so keeps "the file is unreadable" from being misreported as "the
        # file is not valid YAML".
        cause = exc.__cause__
        if isinstance(cause, OSError):
            raise ConfigError(f"notify.yaml: cannot be read: {cause.strerror}") from None
        raise ConfigError("notify.yaml: not valid YAML") from None
    return {**NOTIFY_DEFAULT, "events": list(NOTIFY_DEFAULT["events"]), **overrides}


def save_notify(path: str | Path, notify: dict) -> None:
    """Written 0600 — `write_yaml` stages through `mkstemp`, which creates at
    0600, and `os.replace` carries that mode onto the target."""
    write_yaml(path, {k: notify.get(k, v) for k, v in NOTIFY_DEFAULT.items()})


# ── auto-intake ──────────────────────────────────────────────────────────────

INTAKE_DEFAULT: dict = {
    "enabled": False,
    "interval_s": 300,
    "max_concurrent": 1,
    "repos": [],
    "priority_ceiling": 2,
}


def load_intake(path: str | Path) -> dict:
    """`intake.yaml`, with every missing key defaulted. A missing file is off."""
    return {**INTAKE_DEFAULT, **read_yaml(path, INTAKE_DEFAULT)}
