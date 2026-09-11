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
from collections.abc import Iterator
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from contextlib import contextmanager
from pathlib import Path

import yaml

from kraft import steering as _steering
from kraft.store.repos import ROOT_MERGE_POLICIES

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


def _normalize_test_scopes(scopes: object) -> list[dict] | None:
    """Validated `test_scopes`, or None if the entry has none of its own.

    Deliberately does *not* synthesize a `["**"]` scope from a legacy
    `test_command` here: that synthesis used to be written into the loaded
    entry, and any route that then re-saved repos.yaml (`PATCH`/`DELETE
    /repos`) persisted it -- baking in whatever `test_command` was current at
    load time. A later edit to `test_command` left that stale synthesized
    scope in place, silently overriding the edit forever (verify finding,
    Kraft-9wzy). Callers that need the wrap (`executor.py`) do it themselves
    at the point of use instead, from the un-synthesized `test_command` this
    function leaves untouched.
    """
    if scopes is None:
        return None
    if not isinstance(scopes, list) or not scopes:
        raise ConfigError("repos.yaml: 'test_scopes' must be a non-empty list of mappings")
    for s in scopes:
        if not isinstance(s, dict):
            raise ConfigError("repos.yaml: 'test_scopes' entries must be mappings")
        paths = s.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(x, str) for x in paths):
            raise ConfigError(
                "repos.yaml: 'test_scopes' entry needs a non-empty list of string 'paths'"
            )
        command = s.get("command")
        if not isinstance(command, str) or not command:
            raise ConfigError("repos.yaml: 'test_scopes' entry needs a non-empty string 'command'")
    return scopes


def _normalize_submodules(raw: object) -> list[dict]:
    """Per-submodule settings a repo entry carries alongside the paths its own
    `.gitmodules` names at probe time (design 25 "SUBMODULES"): whether the
    submodule is declarable on a work item, its own test command override, and
    its own chain override. Never populated from a probe — an operator turns
    these on by hand, one row per submodule path they choose to manage here.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("repos.yaml: 'submodules' must be a list of mappings")
    out = []
    for s in raw:
        if not isinstance(s, dict) or not isinstance(s.get("path"), str) or not s["path"]:
            raise ConfigError("repos.yaml: every 'submodules' entry needs a string 'path'")
        enabled = s.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"repos.yaml: submodule {s['path']!r} 'enabled' must be a boolean")
        test_command = s.get("test_command")
        if test_command is not None and not isinstance(test_command, str):
            raise ConfigError(
                f"repos.yaml: submodule {s['path']!r} 'test_command' must be a string"
            )
        chain_override = s.get("chain_override")
        if chain_override is not None and not isinstance(chain_override, str):
            raise ConfigError(
                f"repos.yaml: submodule {s['path']!r} 'chain_override' must be a string"
            )
        out.append(
            {
                "path": s["path"],
                "enabled": enabled,
                "test_command": test_command,
                "chain_override": chain_override,
            }
        )
    return out


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
        # The command CI runs for this repo. The registry's `on.test.run`
        # binding is one command for every repo on the install, which is what
        # lets verify and CI drift apart (Kraft-579). None keeps the registry's
        # command, so an install that never sets one is unchanged.
        r.setdefault("test_command", None)
        if r.get("test_command") is not None and not isinstance(r["test_command"], str):
            raise ConfigError("repos.yaml: 'test_command' must be a string")
        r["test_scopes"] = _normalize_test_scopes(r.get("test_scopes"))
        for key in ("deny_tools", "steering"):
            v = r.setdefault(key, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"repos.yaml: {key!r} must be a list of strings")
        r.setdefault("allow_cross_repo", False)
        if not isinstance(r["allow_cross_repo"], bool):
            raise ConfigError("repos.yaml: 'allow_cross_repo' must be a boolean")
        r.setdefault("default_root_merge_policy", "bump")
        if r["default_root_merge_policy"] not in ROOT_MERGE_POLICIES:
            raise ConfigError(
                f"repos.yaml: 'default_root_merge_policy' must be one of "
                f"{sorted(ROOT_MERGE_POLICIES)}"
            )
        r["submodules"] = _normalize_submodules(r.get("submodules"))
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


@contextmanager
def main_ignore_args(repo: Path) -> Iterator[list[str]]:
    """`-c core.excludesFile=<scratch>`, naming a temp file holding `origin/
    main`'s current `.gitignore` -- or `[]` if there is no `origin/main`, no
    `.gitignore` there, or git refuses to say.

    Layered on top of whatever `.gitignore` is actually checked out in
    `repo`, not a replacement for it -- `core.excludesFile` is git's own
    mechanism for an extra, untracked set of ignore rules, so this only ever
    widens what a `git status`/`git add` in `repo` treats as ignored, never
    narrows it.

    A worktree's checked-out `.gitignore` is whatever `main` looked like when
    `ensure_worktree` cut the worktree, and nothing refreshes it afterward
    short of a full rebase, which most nodes never trigger. A rule `main`
    gains later (Kraft-vu26: `.engineering/` widened past `sessions/`, then
    `docs/superpowers/` added) is invisible to that worktree's `git status`/
    `git add` until then, so whatever a node writes to the now-ignored path
    stages and commits exactly as if the rule had never landed, and rides
    into the merge request -- caught live on work item 46ef3286, whose
    worktree predated the `docs/superpowers/` rule by under an hour. Reading
    `main`'s own copy from its remote-tracking ref sidesteps the lag outright:
    it does not matter how old the branch's checkout is.
    """
    content = git_read(repo, "show", "origin/main:.gitignore", expected_failure=True, strip=False)
    if not content:
        yield []
        return
    with tempfile.NamedTemporaryFile("w", prefix="kraft-main-gitignore-", suffix=".txt") as f:
        f.write(content)
        f.flush()
        yield ["-c", f"core.excludesFile={f.name}"]


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


def normalized_repo_root(p: Path) -> Path | None:
    """`p`'s repo root, with a linked worktree normalized to its main checkout.

    `--show-toplevel` from a linked worktree is the worktree itself, so
    walking straight up from it would never reach the main checkout a
    connected repo is registered under — and agents run in worktrees
    (Kraft-97e, Kraft-tc33). `--git-common-dir` points at the main checkout's
    `.git`, whose parent is that checkout. In an ordinary checkout it is
    `<repo>/.git`, so this is not a worktree special case and the answer is
    unchanged.

    The `.git` name guard is load-bearing: a submodule's common dir is
    `<super>/.git/modules/<path>`, whose parent is not a repo at all. Anything
    that is not a plain `.git` directory stays on `--show-toplevel`.

    None when `p` is not inside a git repository at all.
    """
    toplevel = git_read(p, "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        return None
    common = git_read(
        p, "rev-parse", "--path-format=absolute", "--git-common-dir", expected_failure=True
    )
    return Path(common).parent if common and Path(common).name == ".git" else Path(toplevel)


def _first_test_command(directory: Path) -> str | None:
    return next((cmd for marker, cmd in _TEST_COMMANDS if (directory / marker).is_file()), None)


def _probe_test_scopes(root: Path) -> tuple[str | None, list[dict]]:
    """(legacy singular `test_command`, `test_scopes` list) for `root` (design §2).

    Walks the same `_TEST_COMMANDS` markers, but at the repo root *and* one
    level under it -- deliberately shallow, matching both Kraft's own repo
    (`pyproject.toml` at root, `package.json` under `frontend/`) and the
    common monorepo layout. A marker match in a subdirectory becomes a
    "nested scope"; the root scope's `paths` then excludes every directory a
    nested scope claimed, so a frontend-only diff cannot also match the
    backend scope. No nested scopes -> unchanged single-stack behavior
    (`paths: ["**"]`).
    """
    root_command = _first_test_command(root)
    try:
        subdirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        subdirs = []
    nested = [(d.name, cmd) for d in subdirs if (cmd := _first_test_command(d)) is not None]

    if not nested:
        scopes = [{"paths": ["**"], "command": root_command}] if root_command else []
        return root_command, scopes

    claimed = {name for name, _ in nested}
    try:
        # `/**` on every directory: fnmatch has no notion of "this dir and
        # everything under it", so a bare "src" never matches a changed path
        # like "src/foo.py" and a backend-only diff fails open to every scope,
        # nested ones included (verify finding, Kraft-9wzy). A top-level file
        # (e.g. "pyproject.toml") has no children to cover, so it stays literal.
        top_level = sorted(
            f"{p.name}/**" if p.is_dir() else p.name
            for p in root.iterdir()
            if p.name not in claimed
        )
    except OSError:
        top_level = []
    scopes = []
    if root_command:
        scopes.append({"paths": top_level, "command": root_command})
    scopes.extend({"paths": [f"{name}/**"], "command": cmd} for name, cmd in nested)
    return root_command or nested[0][1], scopes


def probe_repo(path: str | Path) -> dict:
    """What Kraft can tell about a candidate repo without changing anything.

    Read-only on purpose (design 5a): "Kraft flags it but does not change repo
    files". Everything is best-effort — a field it cannot determine comes back
    None or empty rather than failing the probe.
    """
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ConfigError(f"{p} is not a directory")
    root = normalized_repo_root(p)
    if root is None:
        raise ConfigError(f"{p} is not a git repository")

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

    test_command, test_scopes = _probe_test_scopes(root)

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
        "test_scopes": test_scopes,
        "forge": forge,
        "project": project,
    }


# ── access ───────────────────────────────────────────────────────────────────

ACCESS_DEFAULT: dict = {
    "bind": "127.0.0.1",
    "port": 8765,
    "password_hash": None,
    "session_expiry_days": 7,
    "allowed_hosts": [],
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
    "repos": [],
    "priority_ceiling": 2,
}


def load_intake(path: str | Path) -> dict:
    """`intake.yaml`, with every missing key defaulted. A missing file is off."""
    return {**INTAKE_DEFAULT, **read_yaml(path, INTAKE_DEFAULT)}
