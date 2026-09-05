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
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path.name}: cannot read/parse: {exc}") from exc
    if data is None:
        return dict(default or {})
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a mapping at the top level")
    return data


def write_yaml(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── repos ────────────────────────────────────────────────────────────────────

REPOS_DEFAULT: dict = {"repos": []}


def _normalize_forge(repo: dict) -> None:
    """Read a pre-forge repos.yaml entry in place.

    `templates/` is seeded once and never overwritten, so compatibility for the
    `gitlab_project` -> `forge`/`project` rename lives in the reader rather than
    in a migration that would rewrite a file the user owns.
    """
    legacy = repo.pop("gitlab_project", None)
    if repo.get("forge") is None and legacy:
        repo["forge"] = "gitlab"
        repo["project"] = legacy
    repo.setdefault("forge", None)
    repo.setdefault("project", None)


def load_repos(path: str | Path) -> list[dict]:
    data = read_yaml(path, REPOS_DEFAULT)
    repos = data.get("repos") or []
    if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
        raise ConfigError("repos.yaml: 'repos' must be a list of mappings")
    for r in repos:
        if not isinstance(r.get("path"), str) or not r["path"]:
            raise ConfigError("repos.yaml: every repo needs a string 'path'")
        _normalize_forge(r)
    return repos


def save_repos(path: str | Path, repos: list[dict]) -> None:
    write_yaml(path, {"repos": repos})


def git_read(cwd: Path, *args: str) -> str | None:
    """One read-only git command, or None if git says no. Never raises.

    `--no-optional-locks`: a plain `git status`/`diff` still touches
    `.git/index`'s mtime (refreshing stat data), which collides with an
    `index.lock` an agent is holding mid-run. This flag skips that refresh;
    content reads are unaffected, only the lock-taking side effect is.
    """
    cmd = ["git", "--no-optional-locks", *args]
    try:
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git_read could not run %s in %s: %s", cmd, cwd, exc)
        return None
    if out.returncode != 0:
        logger.warning("git_read %s in %s failed: %s", cmd, cwd, out.stderr.strip())
        return None
    return out.stdout.strip()


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
    toplevel = git_read(p, "rev-parse", "--show-toplevel")
    if toplevel is None:
        raise ConfigError(f"{p} is not a git repository")
    root = Path(toplevel)

    submodules: list[str] = []
    gitmodules = root / ".gitmodules"
    if gitmodules.is_file():
        parser = ConfigParser()
        try:
            # .gitmodules is INI-shaped: [submodule "libs/x"] with a path key
            parser.read_string(gitmodules.read_text())
            submodules = sorted(
                parser.get(s, "path") for s in parser.sections() if parser.has_option(s, "path")
            )
        except ConfigParserError, OSError:
            submodules = []

    beads = root / ".beads"
    beads_config = read_yaml(beads / "config.yaml", {}) if beads.is_dir() else {}
    export = beads_config.get("export") or {}

    remote = git_read(root, "remote", "get-url", "origin") or ""
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
