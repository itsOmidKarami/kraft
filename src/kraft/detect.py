"""What `kraft repo connect` proposes as a repository's setup and test commands.

A proposal, never a decision: `config.probe_repo` hands it to the connect
route, which writes it into repos.yaml for a person to check, and nothing at
run time reads this module (`builtins.run_setup_command` runs what is
declared and infers nothing).

Three kinds of evidence, in the order a repository is most likely to mean them:

- **runner**: a task runner the repository wrote itself -- a justfile recipe,
  a Makefile target, a Taskfile or mise task, `script/test`. Whatever tool
  sits underneath, the repository already said how it wants to be driven.
- **toolchain**: an ecosystem's default, picked by its lockfile, so a pnpm
  repository gets `pnpm`, a Poetry one `poetry`, and a pyproject.toml holding
  only ruff settings is not taken for a Python project.
- **ci**: the commands its CI runs (`.github/workflows`, `.gitlab-ci.yml` and
  the like). CI running what a runner or toolchain proposes corroborates it;
  a CI line is proposed itself only where neither has a test command. A CI
  job runs one slice of a matrix, a lint step that happens to say "test", or
  a tool CI installed on its own PATH, so it is weaker evidence than the
  repo's own `test` script. A setup line from CI is shown and never chosen,
  since CI installs tools a worktree must not.

A `.devcontainer`'s create commands are a last resort for setup ("devenv").

The table of detectors is data: `detectors.yaml` beside this module, and an
operator's `$KRAFT_HOME/templates/detectors.yaml` on top of it (`load`). The
readers that list a runner's tasks are code (`_TASK_READERS`), so a detector
names one of them rather than bringing its own parser.

Directories are read from git's own listing (tracked plus untracked, ignored
left out), so `node_modules/`, `.venv/` and `target/` never look like
projects. Directories up to `max_depth` below the root can be scopes of their
own, unless a workspace root above them already covers them (a pnpm
workspace, a Cargo `[workspace]`, a multi-module Maven or Gradle build).
"""

from __future__ import annotations

import fnmatch
import itertools
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kraft.config import ConfigError, bounded_yaml, first_error, git_read, read_yaml

#: The packaged table. An operator's file of the same name under the
#: templates directory layers on top of it, never replaces it wholesale, so a
#: newer Kraft's detectors still reach an install whose file predates them.
PACKAGED = Path(__file__).with_name("detectors.yaml")
FILE = "detectors.yaml"

Tier = Literal["runner", "ci", "toolchain", "devenv"]
#: Which evidence wins, per role. CI setup is evidence only: CI installs
#: global tools (`npm i -g`, `pip install poetry`) a worktree must not.
TEST_TIERS: tuple[Tier, ...] = ("runner", "toolchain", "ci")
SETUP_TIERS: tuple[Tier, ...] = ("runner", "toolchain")

_ID = r"^[a-z][a-z0-9_-]*$"
_TEXT_LIMIT = 4_000_000
#: Bytes one probe reads in all, across every file.
_READ_BUDGET = 64_000_000
#: YAML nodes and CI script lines one probe walks in all, across every
#: file, and in any one file. YAML aliases are shared when composed but
#: walked once per use, so a few hundred bytes of nested aliases would
#: otherwise walk billions of nodes.
_YAML_BUDGET = 200_000
_CI_FILE_BUDGET = 50_000


# ── the table ────────────────────────────────────────────────────────────────


def _check_patterns(v: dict[str, str]) -> dict[str, str]:
    """Every `contains` value compiles, so a typo is refused when the file is
    read rather than raising mid-probe."""
    for glob, pattern in v.items():
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{glob}: {pattern!r} is not a valid regex: {exc}") from exc
    return v


class Condition(BaseModel):
    """Holds when any `files` glob matches (or none is given), every
    `contains` glob names some file its regex matches, and every `toml` glob
    names some TOML file with, at each dotted key given, a value whose JSON
    its regex matches: `{pyproject.toml: {project.optional-dependencies:
    pytest}}`. A key reads one table, where a regex over the text would run
    from that table's header to the end of the file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    files: list[str] = []
    contains: dict[str, str] = {}
    toml: dict[str, dict[str, str]] = {}

    @field_validator("contains")
    @classmethod
    def _patterns(cls, v: dict[str, str]) -> dict[str, str]:
        return _check_patterns(v)

    @field_validator("toml")
    @classmethod
    def _toml_patterns(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for keys in v.values():
            _check_patterns(keys)
        return v


class Command(BaseModel):
    """One proposal a detector can make. `tasks`: names to look for among the
    tasks the detector's reader finds, the first present substituted for
    `{task}`. `when`: any one of these conditions must hold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    tasks: list[str] = []
    when: list[Condition] = []
    #: The files it reads, named as its source; when not given, the first
    #: of the detector's `files` there is.
    reads: list[str] = []


class Detector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_ID)
    tier: Literal["runner", "toolchain"]
    #: Which ecosystem: a workspace root covers the members of its own family.
    family: str | None = None
    files: list[str] = Field(min_length=1)
    unless: list[str] = []
    contains: dict[str, str] = {}
    #: The reader that lists this runner's tasks (`_TASK_READERS`).
    tasks: str | None = None
    #: When a directory this detector matched is a workspace root.
    workspace: list[Condition] = []
    #: Its `files` are lockfiles: a workspace member it matches has its own
    #: lockfile, and installs on its own.
    lockfile: bool = False
    #: A project Kraft has no safe command for (a pyproject.toml with no
    #: lockfile, which `uv sync` and `uv run` would write one for). Where it
    #: matches and nothing else of its family does, the directory proposes no
    #: test and only lockfile installs, and the repo proposes no test command
    #: unless one is given. `reason` says why, to the person connecting.
    stop: bool = False
    reason: str | None = None
    #: How a command runs inside this toolchain's environment (`uv run
    #: {command}`): a runner's test task that calls `pytest` or `python`
    #: bare is run through it, since a worker's PATH has no virtualenv on it.
    wrap: str | None = Field(default=None, pattern=r"\{command\}")
    #: The virtualenv its setup makes, relative to the directory (`.venv`):
    #: a test command another detector proposes beside it (a Makefile's
    #: `make test`, CI's `python -m unittest`) is run with it active, or it
    #: would import from whatever Python a worker's PATH has.
    venv: str | None = Field(default=None, pattern=r"^[\w.-]+(?:/[\w.-]+)*$")
    test: list[Command] = []
    setup: list[Command] = []

    @field_validator("contains")
    @classmethod
    def _patterns(cls, v: dict[str, str]) -> dict[str, str]:
        return _check_patterns(v)

    @field_validator("tasks")
    @classmethod
    def _known_reader(cls, v: str | None) -> str | None:
        if v is not None and v not in _TASK_READERS:
            raise ValueError(
                f"unknown task reader {v!r}; one of {', '.join(sorted(_TASK_READERS))}"
            )
        return v

    @model_validator(mode="after")
    def _a_stop_says_why(self) -> Detector:
        if self.stop and not self.reason:
            raise ValueError(f"{self.id}: a `stop` detector needs a `reason`")
        return self

    @model_validator(mode="after")
    def _tasks_need_a_reader(self) -> Detector:
        if self.tasks is None and any(c.tasks for c in (*self.test, *self.setup)):
            raise ValueError(
                f"{self.id}: a command names `tasks` but the detector has no `tasks` reader"
            )
        return self


class DetectorFile(BaseModel):
    """`detectors.yaml`, packaged or an operator's."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_depth: int | None = Field(default=None, ge=0, le=4)
    ignore_dirs: list[str] = []
    #: Packaged detector ids an operator's file switches off.
    disable: list[str] = []
    detectors: list[Detector] = []


@dataclass(frozen=True)
class Table:
    detectors: tuple[Detector, ...]
    ignore_dirs: frozenset[str]
    max_depth: int


def _read_file(path: Path) -> DetectorFile:
    try:
        return DetectorFile.model_validate(read_yaml(path, {}))
    except ValidationError as exc:
        raise ConfigError(first_error(exc, path.name)) from exc


def load(templates_dir: Path | None = None) -> Table:
    """The packaged table with the operator's `detectors.yaml` on top: its new
    detectors tried first, one reusing a packaged `id` replacing it where it
    stood, `disable` dropping packaged ones. A broken file raises
    `ConfigError`, naming it -- silently ignoring it would propose the very
    commands the operator wrote it to correct."""
    packaged = _read_file(PACKAGED)
    own = _read_file(templates_dir / FILE) if templates_dir else DetectorFile()
    by_id = {d.id: d for d in own.detectors}
    known = {d.id for d in packaged.detectors}
    if len(by_id) != len(own.detectors):
        twice = sorted(
            {d.id for d in own.detectors if [o.id for o in own.detectors].count(d.id) > 1}
        )
        raise ConfigError(f"{FILE}: detector id {', '.join(twice)} is given twice")
    unknown = sorted(set(own.disable) - known)
    if unknown:
        raise ConfigError(f"{FILE}: disable names no packaged detector: {', '.join(unknown)}")
    merged = [d for d in own.detectors if d.id not in known]
    merged += [by_id.get(d.id, d) for d in packaged.detectors if d.id not in own.disable]
    return Table(
        detectors=tuple(merged),
        ignore_dirs=frozenset(packaged.ignore_dirs) | frozenset(own.ignore_dirs),
        max_depth=own.max_depth if own.max_depth is not None else (packaged.max_depth or 0),
    )


# ── the repository, as git lists it ─────────────────────────────────────────


#: How long listing a repository may take before the probe gives up and says
#: so, rather than reading a timeout as a repository with no files.
_LIST_TIMEOUT_S = 120


def source_ref(root: Path) -> str | None:
    """The commit a work item's worktree would be cut from: origin's default
    branch when the clone has it (`builtins.upstream_head` forks from origin,
    not from the checkout, and `git.default_branch` reads `origin/HEAD` or
    falls back to `main`), else the checkout's HEAD. None for a repository
    with no commit yet."""
    target = git_read(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", expected_failure=True)
    for ref in (target or "refs/remotes/origin/main", "HEAD"):
        if git_read(
            root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", expected_failure=True
        ):
            return ref
    return None


def no_commit(path: str | Path) -> str:
    """Why a repository with no commit (`source_ref` None) is not connected:
    a work item's branch would be an empty orphan."""
    return (
        f"{path} has no commit yet, and a work item's branch starts from one: "
        "commit its files, then connect it"
    )


#: A partial clone's missing blob is fetched on demand; a probe must not
#: reach the network, let alone prompt for credentials.
_GIT_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


def _git(root: Path, *args: str) -> bytes:
    """A read the probe cannot do without: a failure is an error naming git's
    reason, never an empty answer that reads as an empty repository."""
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=root,
            capture_output=True,
            timeout=_LIST_TIMEOUT_S,
            check=False,
            env={**os.environ, **_GIT_ENV},
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            f"git {args[0]} in {root} took more than {_LIST_TIMEOUT_S}s; "
            "connect it with --test-command and --setup-command instead"
        ) from exc
    if done.returncode != 0:
        why = done.stderr.decode("utf-8", "replace").strip()
        raise ConfigError(f"git {args[0]} in {root} failed: {why}")
    return done.stdout


class _Blobs:
    """One `git cat-file --batch` for every blob a probe reads: a process
    per file took seconds on a repository with a Makefile in every
    directory."""

    def __init__(self, root: Path):
        self.root = root
        self._proc: subprocess.Popen | None = None

    def read(self, oid: str, limit: int = _TEXT_LIMIT) -> bytes:
        """At most `limit` bytes of a blob, however large it is; empty when
        git cannot give it (a partial clone's missing blob)."""
        try:
            if self._proc is None:
                self._proc = subprocess.Popen(
                    [
                        "git",
                        "--no-optional-locks",
                        "-c",
                        "core.fsmonitor=false",
                        "cat-file",
                        "--batch",
                    ],
                    cwd=self.root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, **_GIT_ENV},
                )
            stdin, stdout = self._proc.stdin, self._proc.stdout
            assert stdin is not None and stdout is not None
            stdin.write(f"{oid}\n".encode())
            stdin.flush()
            header = stdout.readline().split()  # `<oid> blob <size>`, or `<oid> missing`
            if len(header) != 3:
                if not header:  # git is gone: the next read starts another
                    self.close()
                return b""
            size = int(header[2])
            raw = stdout.read(min(size, limit)) if limit > 0 else b""
            left = size - len(raw) + 1  # and the newline after it
            while left > 0 and (chunk := stdout.read(min(left, 1 << 20))):
                left -= len(chunk)
            return raw
        except (OSError, ValueError):
            self.close()
            return b""

    def close(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            for pipe in (self._proc.stdin, self._proc.stdout):
                if pipe is not None:
                    pipe.close()
            self._proc = None


class _Index:
    """Every file of the commit a work item's worktree is cut from
    (`source_ref`), by directory: what the worktree will hold, never the
    working copy's untracked files, uncommitted edits or symlinks. A
    submodule (a gitlink) and a symlink are not files here. A repository
    with no commit yet is read from its working copy instead: tracked plus
    untracked files git does not ignore."""

    def __init__(self, root: Path):
        self.root = root
        self.ref = source_ref(root)
        self._blobs: dict[str, tuple[str, bool]] = {}
        self._reader = _Blobs(root)
        if self.ref is not None:
            listed = _git(root, "ls-tree", "-r", "-z", "--full-tree", self.ref)
            for entry in listed.decode("utf-8", "surrogateescape").split("\0"):
                meta, _, rel = entry.partition("\t")
                mode, kind, oid = (meta.split() + ["", "", ""])[:3]
                if kind == "blob" and mode in ("100644", "100755"):
                    self._blobs[rel] = (oid, mode == "100755")
            paths = list(self._blobs)
        else:
            listed = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            paths = [
                p
                for p in listed.decode("utf-8", "surrogateescape").split("\0")
                if p and (root / p).is_file() and not (root / p).is_symlink()
            ]
        self.files: set[str] = set()
        self.children: dict[str, set[str]] = {"": set()}
        self.subdirs: dict[str, set[str]] = {"": set()}
        for rel in paths:
            self.files.add(rel)
            parent, _, name = rel.rpartition("/")
            self.children.setdefault(parent, set()).add(name)
            while parent:
                up, _, base = parent.rpartition("/")
                self.subdirs.setdefault(up, set()).add(base)
                self.children.setdefault(parent, set())
                parent = up
        self._text: dict[str, str] = {}
        self.tomls: dict[str, dict] = {}
        #: Bytes left to read in this probe, across every file: thousands of
        #: directories each holding a 4 MB file would otherwise be read, and
        #: kept, whole.
        self.read_left = _READ_BUDGET
        #: YAML nodes and CI script lines left in this probe, across every
        #: file, so a hundred large workflows cost what one may.
        self.yaml_left = [_YAML_BUDGET]

    @property
    def dirs(self) -> set[str]:
        return set(self.children)

    def matching(self, d: str, glob: str) -> list[str]:
        """Files under `d` (paths relative to it) that `glob` names. A glob
        with no `/` matches `d`'s own files only."""
        if "/" not in glob:
            return sorted(n for n in self.children.get(d, ()) if fnmatch.fnmatchcase(n, glob))
        if not any(c in glob for c in "*?["):
            return [glob] if _join(d, glob) in self.files else []
        prefix = f"{d}/" if d else ""
        return sorted(
            rel
            for p in self.files
            if p.startswith(prefix) and fnmatch.fnmatchcase(rel := p[len(prefix) :], glob)
        )

    def text(self, rel: str) -> str:
        """`rel`'s content, at most `_TEXT_LIMIT` bytes of it, as text, and
        nothing once the probe has read `_READ_BUDGET` bytes in all."""
        if rel not in self._text:
            limit = max(0, min(_TEXT_LIMIT, self.read_left))
            if rel in self._blobs:
                raw = self._reader.read(self._blobs[rel][0], limit)
            elif rel in self.files and limit:
                try:
                    with open(self.root / rel, "rb") as f:
                        raw = f.read(limit)
                except OSError:
                    raw = b""
            else:
                raw = b""
            self.read_left -= len(raw)
            self._text[rel] = raw[:limit].decode("utf-8", "replace")
        return self._text[rel]

    def yaml(self, rel: str, budget: list[int] | None = None) -> object:
        """`rel` as YAML, bounded by `budget` (a `share` of the probe's):
        past it, a `ValueError`, and nothing is built."""
        return bounded_yaml(self.text(rel), budget if budget is not None else self.share())

    def share(self, cap: int = _CI_FILE_BUDGET) -> list[int]:
        """A budget of at most `cap` for one file, drawn from `yaml_left`,
        which is charged as it is spent."""
        return _Share(self.yaml_left, cap)

    def close(self) -> None:
        self._reader.close()

    def executable(self, rel: str) -> bool:
        if rel in self._blobs:
            return self._blobs[rel][1]
        return rel in self.files and os.access(self.root / rel, os.X_OK)


class _Share(list):
    """A one-file budget, `[n]` like the rest, that is also spent from the
    probe's pool: `share[0]` is the lesser of the two, and spending it
    spends both."""

    def __init__(self, pool: list[int], cap: int):
        super().__init__([0])
        self.pool, self.cap = pool, cap

    def __getitem__(self, i):  # type: ignore[override]
        return min(self.cap, self.pool[0])

    def __setitem__(self, i, value) -> None:  # type: ignore[override]
        spent = self[0] - value
        self.cap -= spent
        self.pool[0] -= spent


def _join(d: str, rel: str) -> str:
    return f"{d}/{rel}" if d else rel


def _holds(index: _Index, d: str, cond: Condition) -> bool:
    if cond.files and not any(index.matching(d, g) for g in cond.files):
        return False
    if not all(_contains(index, d, g, rx) for g, rx in cond.contains.items()):
        return False
    return all(_toml_has(index, d, g, keys) for g, keys in cond.toml.items())


def _toml_has(index: _Index, d: str, glob: str, keys: dict[str, str]) -> bool:
    for rel in index.matching(d, glob):
        data = _toml(index, _join(d, rel))
        values = [_dig(data, key.split(".")) for key in keys]
        if all(
            v is not None and re.search(rx, json.dumps(v))
            for v, rx in zip(values, keys.values(), strict=True)
        ):
            return True
    return False


def _dig(value: object, path: list[str]) -> object:
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _contains(index: _Index, d: str, glob: str, pattern: str) -> bool:
    rx = re.compile(pattern)
    return any(rx.search(index.text(_join(d, rel))) for rel in index.matching(d, glob))


# ── task readers ─────────────────────────────────────────────────────────────

# No pattern here may span lines or nest a quantifier: a committed file is
# attacker-sized (up to `_TEXT_LIMIT`), and a regex that backtracks over it
# holds the GIL, freezing the whole server, not just the probe's thread.
_JUST_RECIPE = re.compile(r"^@?([A-Za-z_][\w-]*+)(?:[ \t][^:\n]*+)?:(?!=)", re.MULTILINE)
#: The rule's names, and trailing blanks the reader splits off.
_MAKE_RULE = re.compile(r"^([^\s:#=][^:#=\n]*+)::?(?!=)", re.MULTILINE)
_RAKE_TASK = re.compile(r"""\btask\s*+\(?+\s*+:?+["']?+([\w:]++)""")
#: `npm init`'s placeholder: a `test` script that only ever fails.
_NPM_STUB = re.compile(r"no test specified", re.IGNORECASE)


def _first(index: _Index, d: str, names: Iterable[str]) -> str | None:
    return next((n for n in names if index.matching(d, n)), None)


#: What reading a committed file can raise: it is malformed, or nested deeper
#: than a parser's recursion goes (`[[[[...` in a package.json). Either way
#: that file is no evidence; the probe goes on without it.
_UNREADABLE = (ValueError, yaml.YAMLError, RecursionError)


def _jsonc(text: str) -> object:
    """JSON with comments and trailing commas, as deno.jsonc and
    devcontainer.json allow."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*+[}\]])", r"\1", "".join(out)))


def _keys(value: object) -> set[str]:
    return {str(k) for k in value} if isinstance(value, dict) else set()


def _read_just(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name = _first(index, d, ("justfile", "Justfile", ".justfile"))
    if name is None:
        return None
    return name, set(_JUST_RECIPE.findall(index.text(_join(d, name)))), "recipe"


def _read_make(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    # make's own order of lookup.
    name = _first(index, d, ("GNUmakefile", "makefile", "Makefile"))
    if name is None:
        return None
    targets = {
        t
        for line in _MAKE_RULE.findall(index.text(_join(d, name)))
        for t in line.split()
        if re.fullmatch(r"[\w.-]+", t) and not t.startswith(".")
    }
    return name, targets, "target"


def _read_taskfile(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    names = ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml", "taskfile.yaml")
    name = _first(index, d, (*names, "Taskfile.dist.yml", "Taskfile.dist.yaml"))
    if name is None:
        return None
    try:
        data = index.yaml(_join(d, name)) or {}
    except _UNREADABLE:
        return None
    return name, _keys(data.get("tasks") if isinstance(data, dict) else None), "task"


def _toml(index: _Index, rel: str) -> dict:
    if rel not in index.tomls:
        try:
            index.tomls[rel] = tomllib.loads(index.text(rel))
        except _UNREADABLE:
            index.tomls[rel] = {}
    return index.tomls[rel]


def _read_mise(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name = _first(index, d, ("mise.toml", ".mise.toml", "mise/config.toml", ".config/mise.toml"))
    if name is None:
        return None
    return name, _keys(_toml(index, _join(d, name)).get("tasks")), "task"


def _json_section(index: _Index, d: str, names: tuple[str, ...], key: str):
    name = _first(index, d, names)
    if name is None:
        return None, None
    text = index.text(_join(d, name))
    try:
        data = _jsonc(text) if name.endswith("c") else json.loads(text)
    except _UNREADABLE:
        return name, None
    return name, (data.get(key) if isinstance(data, dict) else None)


def _read_npm(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name, scripts = _json_section(index, d, ("package.json",), "scripts")
    if name is None:
        return None
    found = _keys(scripts)
    if "test" in found and _NPM_STUB.search(str(scripts.get("test"))):
        found.discard("test")
    return name, found, "script"


def _read_composer(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name, scripts = _json_section(index, d, ("composer.json",), "scripts")
    return None if name is None else (name, _keys(scripts), "script")


def _read_deno(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name, tasks = _json_section(index, d, ("deno.json", "deno.jsonc"), "tasks")
    return None if name is None else (name, _keys(tasks), "task")


def _read_rake(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    name = _first(index, d, ("Rakefile", "rakefile", "Rakefile.rb"))
    if name is None:
        return None
    text = index.text(_join(d, name))
    found = set(_RAKE_TASK.findall(text))
    if "Rake::TestTask" in text or "Minitest::TestTask" in text:
        found.add("test")
    if "RSpec::Core::RakeTask" in text:
        found.add("spec")
    return name, found, "task"


def _pyproject_table(index: _Index, d: str, *keys: str) -> set[str] | None:
    if not index.matching(d, "pyproject.toml"):
        return None
    table: object = _toml(index, _join(d, "pyproject.toml"))
    for key in keys:
        table = table.get(key) if isinstance(table, dict) else None
    return _keys(table)


def _read_pdm(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    found = _pyproject_table(index, d, "tool", "pdm", "scripts")
    return None if found is None else ("pyproject.toml", found, "script")


def _read_poe(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    found = _pyproject_table(index, d, "tool", "poe", "tasks")
    return None if found is None else ("pyproject.toml", found, "task")


def _read_script(index: _Index, d: str) -> tuple[str, set[str], str] | None:
    """Scripts to Rule Them All: executable files under `script/`, `scripts/`
    or `bin/`, named by their path."""
    found = {
        rel
        for top in ("script", "scripts", "bin")
        for name in index.children.get(_join(d, top), ())
        if index.executable(_join(d, rel := f"{top}/{name}"))
    }
    return ("", found, "script") if found else None


#: (file it read, the task names it found, what that runner calls one).
_TASK_READERS: dict[str, Callable[[_Index, str], tuple[str, set[str], str] | None]] = {
    "just": _read_just,
    "make": _read_make,
    "taskfile": _read_taskfile,
    "mise": _read_mise,
    "npm": _read_npm,
    "composer": _read_composer,
    "deno": _read_deno,
    "rake": _read_rake,
    "pdm": _read_pdm,
    "poe": _read_poe,
    "script": _read_script,
}


# ── candidates ───────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """One command the evidence supports, for one directory."""

    dir: str
    role: Literal["test", "setup"]
    command: str
    tier: Tier
    #: What it was read from, for a person: "justfile recipe `test`".
    source: str
    #: The file, relative to the repository root.
    marker: str
    detector: str
    family: str | None = None
    #: CI runs this same command: as strong as a CI line, and more specific.
    corroborated: bool = False
    #: A CI line whose program is a bare tool rather than a runner, a
    #: toolchain's wrapper or a script of the repo's own.
    bare: bool = False
    #: The runner task it runs (`setup`), when it runs one.
    task: str | None = None
    chosen: bool = False


@dataclass
class _Match:
    detector: Detector
    marker: str
    workspace: bool


def _detect(index: _Index, d: str, table: Table) -> tuple[list[Candidate], list[_Match]]:
    found: list[Candidate] = []
    matches: list[_Match] = []
    for det in table.detectors:
        hits = [m for g in det.files for m in index.matching(d, g)]
        if not hits or any(index.matching(d, g) for g in det.unless):
            continue
        if not all(_contains(index, d, g, rx) for g, rx in det.contains.items()):
            continue
        marker = hits[0]
        matches.append(
            _Match(det, _join(d, marker), any(_holds(index, d, c) for c in det.workspace))
        )
        read = _TASK_READERS[det.tasks](index, d) if det.tasks else None
        for role, commands in (("test", det.test), ("setup", det.setup)):
            for cmd in commands:
                if cmd.when and not any(_holds(index, d, c) for c in cmd.when):
                    continue
                if cmd.tasks:
                    if read is None:
                        continue
                    task = next((t for t in cmd.tasks if t in read[1]), None)
                    if task is None:
                        continue
                    file, _, kind = read
                    where = file or task
                    source = f"{_join(d, where)} {kind} `{task}`" if file else _join(d, task)
                    if file and _join(d, file) != _join(d, marker) and det.tier == "toolchain":
                        source += f" with {_join(d, marker)}"
                    found.append(
                        Candidate(
                            d,
                            role,
                            cmd.run.replace("{task}", task),
                            det.tier,
                            source,
                            _join(d, where),
                            det.id,
                            det.family,
                            task=task,
                        )
                    )
                else:
                    reads = [_join(d, r) for r in cmd.reads] or [_join(d, marker)]
                    found.append(
                        Candidate(
                            d,
                            role,
                            cmd.run,
                            det.tier,
                            " + ".join(reads),
                            reads[0],
                            det.id,
                            det.family,
                        )
                    )
                break  # the first command that holds is this detector's proposal
    return found, matches


# ── CI ───────────────────────────────────────────────────────────────────────

_CI_FILES = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    ".buildkite/pipeline.yml",
    ".drone.yml",
    ".woodpecker.yml",
    ".woodpecker/*.yml",
    ".travis.yml",
)
_CI_COMMAND_KEYS = {"run", "script", "commands", "command", "bash", "pwsh", "powershell"}
_CI_DIR_KEYS = ("working-directory", "working_directory", "workingDirectory")
#: Programs that run a project's tests through its own toolchain or scripts. A
#: CI test line starting with anything else (`pytest`, `jest`) runs a tool CI
#: installed on its own PATH.
_CI_WRAPPERS = {
    "just", "make", "task", "mise", "npm", "pnpm", "yarn", "bun", "bunx", "npx", "deno",
    "uv", "uvx", "poetry", "pdm", "pipenv", "hatch", "tox", "nox", "cargo", "go", "gradle",
    "mvn", "sbt", "dotnet", "bundle", "composer", "mix", "swift", "flutter", "dart", "zig",
    "stack", "cabal", "julia", "bazel", "bazelisk", "cmake", "ctest", "meson", "sh", "bash",
}  # fmt: skip
#: A workflow file named for the repo's tests or its CI as a whole.
_CI_TEST_FILE = re.compile(
    r"(?i)^(?:\.gitlab-ci|config|azure-pipelines|.*(?:test|ci|check)[^/]*)\.ya?ml$"
)
#: Workflow files whose tests are not the repo's unit tests, read last.
_CI_LATE = re.compile(r"e2e|playwright|cypress|release|deploy|docs|nightly|publish|pages|bench")
#: Inputs to a step, not commands: `actions/github-script`'s `with: script:`
#: is JavaScript.
_CI_SKIP_KEYS = {"with", "env", "variables", "environment", "rules", "only", "except"}
_CI_SETUP_KEYS = {"before_script", "install", "before_install"}
#: A word that says a line runs tests. `check` and `verify` only behind the
#: runners that use them for that: `ruff check` and `cargo check` do not test.
#: Not a word inside a task name (`typecheck:tests`), nor Gradle's `-x test`,
#: which excludes the tests.
_CI_TEST = re.compile(
    r"(?<![\w./:-])(?<!-x )(?<!--exclude-task )"
    r"(test|tests|pytest|rspec|jest|vitest|phpunit|ctest|tox|nox)(?![\w-])"
    r"|^(make|just|task)\s+check\b|^(\./mvnw|mvn)\b.*\bverify\b"
)
_CI_SETUP = re.compile(
    r"(?<![\w-])(ci|install|sync|restore|deps\.get|fetch|bootstrap|setup|download|get)(?![\w-])"
)
_CI_SETUP_TOOLS = {
    "npm", "pnpm", "yarn", "bun", "uv", "poetry", "pdm", "pipenv", "bundle", "composer",
    "go", "cargo", "dotnet", "mix", "just", "make", "task", "deno", "flutter", "dart", "swift",
}  # fmt: skip
#: Never a project's test or setup step, whatever word follows.
_CI_NOT_COMMANDS = {
    "echo", "printf", "cat", "export", "mkdir", "cp", "mv", "rm", "ls", "curl", "wget", "git",
    "sudo", "apt", "apt-get", "brew", "choco", "test", "[", "if", "then", "fi", "for", "do",
    "done", "set", "source", ".", "chmod", "docker", "gh", "glab", "tee", "apk", "yum", "dnf",
    "pacman", "zypper", "port", "snap",
}  # fmt: skip
_CI_GLOBAL_INSTALL = re.compile(
    r"\s(-g|--global)\b|^(go|cargo) install\b|\btool install\b"
    r"|\bpip3? install\s+(-U\s+|--upgrade\s+)?(pip|uv|poetry|tox|nox|pipenv|pdm|hatch|pre-commit)\b"
)


class _Overrun(ValueError):
    """A CI file past its budget: it is skipped, not read in part."""


def _spend(budget: list[int], n: int = 1) -> None:
    budget[0] -= n
    if budget[0] < 0:
        raise _Overrun


def _ci_lines(value: object, budget: list[int]) -> list[str]:
    if isinstance(value, str):
        # A long script costs by its length, however often an alias repeats it.
        _spend(budget, 1 + len(value) // 64)
        lines = value.replace("\\\n", " ").splitlines()
        _spend(budget, len(lines))
        return lines
    _spend(budget)
    if isinstance(value, list):
        return [line for v in value for line in _ci_lines(v, budget)]
    return []


def _ci_walk(
    node: object, wd: str, out: list[tuple[str, str, list[str]]], budget: list[int]
) -> None:
    """(working directory, key, lines) for every script under `node`: one
    entry per script, since its lines run in one shell, one after another."""
    _spend(budget)
    if isinstance(node, list):
        for v in node:
            _ci_walk(v, wd, out, budget)
        return
    if not isinstance(node, dict):
        return
    defaults = node.get("defaults")
    run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
    if isinstance(run_defaults, dict) and isinstance(run_defaults.get("working-directory"), str):
        wd = run_defaults["working-directory"]
    wd = next((node[k] for k in _CI_DIR_KEYS if isinstance(node.get(k), str)), wd)
    for key, value in node.items():
        if key in _CI_SKIP_KEYS:
            continue
        if key in _CI_COMMAND_KEYS and isinstance(value, dict):
            _ci_walk(value, wd, out, budget)  # CircleCI's long form: `run: {command: ...}`
        elif key in _CI_COMMAND_KEYS or key in _CI_SETUP_KEYS:
            out.append((wd, str(key), _ci_lines(value, budget)))
        else:
            _ci_walk(value, wd, out, budget)


def _relative_dir(base: str, step: str) -> str | None:
    """`step` from `base`, relative to the repository root; None when it
    leaves the repository or cannot be known."""
    d = posixpath.normpath(_join(base, step) if base else step)
    if d == ".":
        return ""
    if d.startswith("..") or posixpath.isabs(d) or "$" in d:
        return None
    return d


def _and_parts(line: str) -> list[str] | None:
    """`line` cut at each `&&` outside quotes; None when its quotes do not
    balance, which no part of it can be read without."""
    parts, current, quote, i = [], [], None, 0
    while i < len(line):
        c = line[i]
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif line.startswith("&&", i):
            parts.append("".join(current))
            current, i = [], i + 2
            continue
        current.append(c)
        i += 1
    if quote:
        return None
    parts.append("".join(current))
    return parts


def _ci_split(wd: str | None, line: str) -> tuple[list[tuple[str | None, str]], str | None]:
    """`cd frontend && npm test` -> [("frontend", "npm test")]: each `&&` part
    with the directory a `cd` before it moved to, and the directory the line
    leaves the shell in -- the next line of the same script starts there."""
    out: list[tuple[str | None, str]] = []
    here: str | None = wd
    for part in _and_parts(line) or []:
        part = part.strip()
        words = part.split()
        if len(words) == 2 and words[0] == "cd":
            here = _relative_dir(here, words[1]) if here is not None else None
            continue
        moved = _PREFIX.match(part)
        if moved and here is not None:
            part = f"{moved['tool']} {moved['before']}{moved['after']}".strip()
            out.append((_relative_dir(here, moved["dir"]), re.sub(r"\s+", " ", part)))
            continue
        out.append((here, part))
    return out, here


#: `npm --prefix web ci`, `pnpm --dir web test`, `yarn --cwd web test`: a
#: package manager told which directory to run in.
_PREFIX = re.compile(
    r"^(?P<tool>npm|pnpm|yarn)\s++(?P<before>.*?)(?:--prefix|--dir|--cwd|-C)[=\s](?P<dir>\S++)(?P<after>.*)$"
)


def _shell_syntax(command: str) -> bool:
    """A variable, a pipe, a redirect, a `case` arm: a line that means
    something only inside the script around it, or only on CI."""
    if "$" in command or "`" in command:  # expanded inside double quotes too
        return True
    try:
        shlex.split(command)
    except ValueError:  # an unbalanced quote: the command runs as argv, and cannot
        return True
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    return bool(re.search(r"[|;&<>(){}\\]", bare))


def _strip_env(command: str) -> str:
    """`CI=true npm test` -> `npm test`: a test command runs without a shell,
    where a leading assignment is a program name."""
    words = command.split(" ")
    n = 0
    while n < len(words) and _ASSIGNMENT.fullmatch(words[n]):
        n += 1
    return " ".join(words[n:])


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*+=\S*+")


def _ci_candidates(index: _Index) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[tuple[str, str, str]] = set()
    # The repo's own test workflow before its e2e, release or docs ones, and
    # before the dedup, so a command both run is credited to the first.
    files = [rel for glob in _CI_FILES for rel in index.matching("", glob)]
    files.sort(key=lambda rel: bool(_CI_LATE.search(posixpath.basename(rel))))
    for rel in files:
        scripts: list[tuple[str, str, list[str]]] = []
        budget = index.share()
        try:
            _ci_walk(index.yaml(rel, budget), "", scripts, budget)
        except _UNREADABLE:
            continue
        lines = []
        for wd, key, block in scripts:
            here = _relative_dir("", wd) if wd else ""
            for raw in block:  # a `cd` on one line holds for the lines after it
                parts, here = _ci_split(here, raw.strip())
                lines += [(key, d, part) for d, part in parts]
        for key, d, part in lines:
            if d is None:
                continue
            command = _strip_env(part.strip())
            # A control or bidi character could show a person one command
            # in a terminal while argv runs another.
            if not command or command.startswith("#") or not command.isprintable():
                continue
            if _shell_syntax(command):
                continue
            head = command.split()[0]
            if head in _CI_NOT_COMMANDS or "--version" in command or "--help" in command:
                continue
            if d and d not in index.children:
                continue
            verb = command.split()[1] if len(command.split()) > 1 else ""
            if key in _CI_SETUP_KEYS or (
                head in _CI_SETUP_TOOLS
                # `uv sync --group tests`, `mix deps.get --only test`: the
                # tool's own install, whatever its arguments name.
                and (
                    _CI_SETUP.fullmatch(verb)
                    or (_CI_SETUP.search(command) and not _CI_TEST.search(command))
                )
            ):
                role = "setup"
                if _CI_GLOBAL_INSTALL.search(command):
                    continue
            elif _CI_TEST.search(command):
                role = "test"
            else:
                continue
            if (d, role, command) in seen:
                continue
            seen.add((d, role, command))
            bare = role == "test" and not (
                head in _CI_WRAPPERS or head.startswith(("./", "bin/", "script"))
            )
            found.append(Candidate(d, role, command, "ci", rel, rel, "ci", bare=bare))
    return found


# ── devcontainer ─────────────────────────────────────────────────────────────

_DEVCONTAINER = (".devcontainer/devcontainer.json", ".devcontainer.json")
_DEVCONTAINER_KEYS = ("onCreateCommand", "updateContentCommand", "postCreateCommand")


def _devcontainer_command(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [shlex.join(value)] if value else []
    if isinstance(value, dict):
        return [c for v in value.values() for c in _devcontainer_command(v)]
    return []


def _devcontainer_candidate(index: _Index) -> Candidate | None:
    rel = next((r for r in _DEVCONTAINER if r in index.files), None)
    if rel is None:
        return None
    try:
        data = _jsonc(index.text(rel))
        if not isinstance(data, dict):
            return None
        keys = [k for k in _DEVCONTAINER_KEYS if k in data]
        parts = [c for k in keys for c in _devcontainer_command(data[k])]
    except _UNREADABLE:
        return None
    # A container's own provisioning is no worktree's to run.
    if not parts or not all(p.isprintable() for p in parts):
        return None
    if any(re.search(r"\b(sudo|apt-get|apt|apk|yum|dnf)\b", p) for p in parts):
        return None
    source = f"{rel} {', '.join(f'`{k}`' for k in keys)}"
    return Candidate("", "setup", " && ".join(parts), "devenv", source, rel, "devcontainer")


# ── assembly ─────────────────────────────────────────────────────────────────


@dataclass
class Scope:
    """One directory proposed as its own unit: its test command and the setup
    that prepares it, both as they run from inside it."""

    dir: str
    test: Candidate | None
    setup: list[Candidate] = field(default_factory=list)
    #: Prepared by a setup above it (the root's runner recipe, or its
    #: workspace's install), so it needs none of its own.
    covered: bool = False
    #: The candidates its test command runs: one, or one per family.
    parts: list[Candidate] = field(default_factory=list)

    @property
    def setup_command(self) -> str | None:
        if self.setup:
            return " && ".join(c.command for c in self.setup)
        return "" if self.covered else None


@dataclass
class Proposal:
    test_command: str | None
    test_scopes: list[dict]
    test_markers: list[str]
    setup_command: str | None
    #: Directories with a test command and no setup to prepare them: what
    #: leaves `setup_command` undecided.
    missing_setup: list[str]
    #: Every directory the proposal covers, root first, in the shape
    #: `combine` reads back when a person picks a different command.
    scopes: list[dict]
    candidates: list[dict]
    #: The commit read (`source_ref`), or None for a repository with none.
    ref: str | None = None
    #: Directories a `stop` detector stopped, with its reason: why there is
    #: no test command when one was not given.
    stopped: list[dict] = field(default_factory=list)
    #: Programs a proposed command runs that this machine's PATH does not
    #: have (hono's `deno`): `{dir, tool}`, said before the first work item
    #: fails on them.
    missing_tools: list[dict] = field(default_factory=list)


def _depth(d: str) -> int:
    return d.count("/") + 1 if d else 0


def _ancestors(d: str) -> list[str]:
    parts = d.split("/")
    return ["", *("/".join(parts[:i]) for i in range(1, len(parts)))] if d else []


def _first_by_tier(cands: list[Candidate], tiers: tuple[Tier, ...]) -> Candidate | None:
    """The first candidate by tier, and within a tier one CI also runs. A CI
    line running a bare tool (`pytest`, `jest`) comes last of all: CI
    installed that tool on its own PATH, which a worktree's is not."""
    for tier in tiers:
        pool = [c for c in cands if c.tier == tier and not c.bare]
        hit = next((c for c in pool if c.corroborated), pool[0] if pool else None)
        if hit is not None:
            return hit
    return next((c for c in cands if c.bare), None) if "ci" in tiers else None


def _setups(
    cands: list[Candidate],
    skip_families: set[str],
    tested: set[str] | None,
    test_files: frozenset[str] = frozenset(),
) -> list[Candidate]:
    """A runner's setup recipe if there is one, else a toolchain's: one for
    each family the test command runs (`npm test` needs `npm ci`, not the
    `cargo fetch` beside it), or one per family when that is not known
    (`tested` None: a runner's `make test` may need both a Python and a
    JavaScript install). A devcontainer's commands are never chosen: they
    are written for a container, and commonly install into its global
    interpreter."""
    runner = _first_by_tier(cands, ("runner",))
    if runner is not None:
        return [runner]
    picked: list[Candidate] = []
    families: set[str | None] = set()
    for c in cands:
        if c.tier != "toolchain" or c.family in families or c.family in skip_families:
            continue
        if tested is None or c.family in tested:
            families.add(c.family)
            picked.append(_with_ci_flags(c, cands, test_files))
    return picked


def _with_ci_flags(
    setup: Candidate, cands: list[Candidate], test_files: frozenset[str]
) -> Candidate:
    """`setup` with the flags CI adds to the same command in the same
    directory, in a workflow that runs tests (`test_files`): fastapi's
    tests need the extras its test workflow's `uv sync --group tests
    --extra all` installs, which a plain `uv sync` leaves out, and its bot
    workflows' `--group github-actions` does not. Only options and their
    values are taken, never another command or argument."""
    want = shlex.split(setup.command)
    for c in cands:
        if c.tier != "ci" or c.role != "setup" or c.marker not in test_files:
            continue
        words = shlex.split(c.command)
        extra = words[len(want) :]
        if words[: len(want)] != want or not extra or not extra[0].startswith("-"):
            continue
        if all(w.startswith("-") or prev.startswith("-") for prev, w in itertools.pairwise(extra)):
            setup.command = c.command
            setup.source += f", with CI's flags ({c.marker})"
            break
    return setup


def _programs(command: str) -> list[str]:
    """The programs `command` runs: each `&&` part's first word, past
    assignments, and through `sh -c`'s script."""
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    out: list[str] = []
    part: list[str] = []
    for word in [*words, "&&"]:
        if word != "&&":
            part.append(word)
            continue
        while part and _ASSIGNMENT.fullmatch(part[0]):
            part.pop(0)
        if part[:2] == ["sh", "-c"] and len(part) > 2:
            out += _programs(part[2])
        elif part:
            out.append(part[0])
        part = []
    return out


def _missing_tools(scopes: list[Scope]) -> list[dict]:
    """The programs proposed commands run that PATH here does not have. A
    path (`./gradlew`, `.venv/bin/python`) is the repo's or its setup's."""
    out: list[dict] = []
    seen: set[str] = set()
    for s in scopes:
        for c in (*s.parts, *s.setup):
            for tool in _programs(c.command):
                if "/" in tool or tool in seen or shutil.which(tool) is not None:
                    continue
                seen.add(tool)
                out.append({"dir": s.dir or ".", "tool": tool})
    return out


def _choose_test(cands: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    """The directory's test command, and the candidates it runs. When the
    best evidence is a toolchain's and toolchains of more than one family
    have a test here (a pnpm workspace beside a Cargo one, a mix project
    with a package.json), it runs each family's, one after another: one
    family's tests passing says nothing of the other's, and a change to
    either is this directory's."""
    tests = [c for c in cands if c.role == "test"]
    first = _first_by_tier(tests, TEST_TIERS)
    if first is None or first.tier != "toolchain":
        return first, [first] if first is not None else []
    by_family: dict[str | None, Candidate] = {first.family: first}
    for c in tests:
        if c.tier == "toolchain" and not c.bare and c.family not in by_family:
            by_family[c.family] = c
    parts = list(by_family.values())
    if len(parts) == 1:
        return first, parts
    joined = " && ".join(c.command for c in parts)
    combined = Candidate(
        first.dir,
        "test",
        f"sh -c {shlex.quote(joined)}",
        "toolchain",
        " + ".join(c.source for c in parts),
        first.marker,
        "+".join(c.detector for c in parts),
    )
    return combined, parts


def _families(test: Candidate | None, parts: list[Candidate]) -> set[str] | None:
    """The families a test command runs: None when not known (a runner's
    task, a given command, a CI line) or when there is none, which is taken
    to run, and need the setup of, them all."""
    if test is None or test.tier != "toolchain":
        return None
    return {c.family for c in parts if c.family}


def in_dir(d: str, command: str, *, shell: bool) -> str:
    """`command` run from `d` inside the worktree. A setup command runs
    through a shell; a test command does not (it is split into argv), so its
    `cd` needs an `sh -c` of its own."""
    if not d:
        return command
    inner = f"cd {shlex.quote(d)} && {command}"
    return f"({inner})" if shell else f"sh -c {shlex.quote(inner)}"


def combine_setup(scopes: list[dict]) -> str | None:
    """The one `setup_command` that prepares every scope: each one's setup,
    from its own directory. None when a scope with a test has no setup --
    an undeclared setup stops the first work item, which is what should
    happen until a person decides."""
    parts = []
    declared = False
    for s in scopes:
        if s.get("setup") is None:
            if s.get("test"):
                return None
            continue
        declared = True
        if s["setup"]:
            parts.append(in_dir(s["dir"], s["setup"], shell=True))
    if parts:
        return " && ".join(parts)
    # Every scope says "nothing to prepare": that is a declaration too.
    return "" if declared else None


def _unclaimed(index: _Index, d: str, claimed: set[str]) -> list[str]:
    """`paths` globs for everything under `d` that no claimed directory owns."""
    out = [_join(d, n) for n in sorted(index.children.get(d, ()))]
    for sub in sorted(index.subdirs.get(d, ())):
        full = _join(d, sub)
        if full in claimed:
            continue
        if any(c.startswith(f"{full}/") for c in claimed):
            out += _unclaimed(index, full, claimed)
        else:
            out.append(f"{full}/**")
    return out


def _uncommented(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _recipe(text: str, runner: str, task: str | None) -> str:
    """What a runner's `task` runs: its own body and the bodies of the tasks
    it depends on, without comments. For a runner whose file this cannot cut
    into recipes (a Taskfile, a script), the whole file without comments."""
    if runner not in ("just", "make") or task is None:
        return _uncommented(text)
    header = _JUST_RECIPE if runner == "just" else _MAKE_RULE
    recipes: dict[str, tuple[list[str], list[str]]] = {}
    current: list[str] | None = None
    for line in _uncommented(text).splitlines():
        if line[:1] in (" ", "\t") or not line.strip():
            if current is not None:
                current.append(line)
            continue
        match = header.match(line)
        current = None
        if match:
            names = match.group(1).split() if runner == "make" else [match.group(1)]
            deps = [re.split(r"[(\s]", w)[0] for w in line.split(":", 1)[1].split()]
            current = []
            for name in names:
                recipes[name] = (current, deps)
    out: list[str] = []
    seen: set[str] = set()
    todo = [task]
    while todo:
        name = todo.pop()
        if name in seen or name not in recipes:
            continue
        seen.add(name)
        body, deps = recipes[name]
        out += body
        todo += deps
    return "\n".join(out)


def _names_dir(text: str, d: str) -> bool:
    """Whether a setup recipe's text works in directory `d`: `cd web`,
    `--prefix web` (or `--dir`, `--cwd`, `-C`), or a path `web/...`. A bare
    word is not enough: it is as likely a comment or another subject."""
    name = rf"(?:\./)?{re.escape(d)}"
    moved = rf"(?:\bcd\s+|(?:--prefix|--dir|--cwd|-C)[=\s]+){name}(?![\w.-])"
    return re.search(rf"{moved}|(?<![\w.-]){name}/", text) is not None


#: A line of a task that runs Python's tools as whatever is first on PATH:
#: `pytest -x`, `python -m coverage`, Taskfile's `- pytest`, mise's `run = "pytest"`.
_BARE_PYTHON = re.compile(
    r"""(?:^|&&|;|\|\|)[ \t]*+(?:cmd:[ \t]*+|run[ \t]*+=[ \t]*+)?+["']?+[@-]*+[ \t]*+"""
    r"(?:[A-Za-z_]\w*+=[^\s;&|]*+[ \t]++)*+(?:python3?|pytest|py\.test|coverage)(?![\w.-])",
    re.MULTILINE,
)


def _in_env(index: _Index, by_dir: dict[str, list[Candidate]], matches: dict[str, list[_Match]]):
    """A runner's test task that runs `pytest` or `python` bare, in a
    directory a Python toolchain with a `wrap` locks, runs through that
    toolchain: `uv run ./scripts/test.sh`. Bare, it would find whatever
    Python a worker's PATH has, without the project's dependencies."""
    for d, cands in by_dir.items():
        env = next((m for m in matches.get(d, ()) if m.detector.wrap), None)
        if env is None:
            continue
        for c in cands:
            if c.tier != "runner" or c.role != "test":
                continue
            if not _BARE_PYTHON.search(_recipe(index.text(c.marker), c.detector, c.task)):
                continue
            c.command = env.detector.wrap.replace("{command}", c.command)
            c.source += f", run through {env.detector.id} ({env.marker})"


def _in_venv(test: Candidate, venv: str) -> None:
    """`test` run with the virtualenv `venv` active: its `python` named
    outright, or anything else run with the venv's `bin` first on PATH --
    `make test` runs whatever `pytest` it finds there. Spelled with `$PWD`,
    since a test command runs from its own directory, and through `sh -c`,
    since a test command is split into argv with no shell to expand it."""
    try:
        words = shlex.split(test.command)
    except ValueError:
        return
    if not words:
        return
    if words[0] in ("python", "python3"):
        test.command = shlex.join([f"{venv}/bin/python", *words[1:]])
    else:
        active = f'VIRTUAL_ENV="$PWD/{venv}" PATH="$PWD/{venv}/bin:$PATH"'
        test.command = f"sh -c {shlex.quote(f'{active} {shlex.join(words)}')}"
    test.source += f", in the setup's {venv}"


def _stop(
    by_dir: dict[str, list[Candidate]], matches: dict[str, list[_Match]], table: Table
) -> dict[str, Detector]:
    """The directories a `stop` detector stops, by reason, with their
    candidates cut to what is still safe there: a runner's own tasks, and
    lockfile installs. A runner's `test` task in the directory, or another
    detector of the stopped family, means it is not stopped at all."""
    locked = {d.id for d in table.detectors if d.lockfile}
    out: dict[str, Detector] = {}
    for d, ms in matches.items():
        stops = [m.detector for m in ms if m.detector.stop]
        others = {m.detector.family for m in ms if not m.detector.stop}
        stop = next((s for s in stops if s.family not in others), None)
        cands = by_dir.get(d, [])
        if stop is None or any(c.role == "test" and c.tier == "runner" for c in cands):
            continue
        out[d] = stop
        by_dir[d] = [
            c for c in cands if c.tier == "runner" or (c.role == "setup" and c.detector in locked)
        ]
    return out


def _covered(
    stopped: dict[str, Detector],
    by_dir: dict[str, list[Candidate]],
    table: Table,
    test_command: str | None,
) -> dict[str, Detector]:
    """The stops that still stop the repo. A stopped subdirectory is covered
    by the root -- its changes match the root scope, whose command runs its
    tests -- when the root's command is given, comes from the repo's own task
    runner (a justfile `test` recipe), or from a toolchain of the stopped
    project's family (a uv workspace's root `uv.lock`, a root `hatch.toml`).
    Then it just gets no scope of its own. A stop at the root itself always
    stands."""
    if "" in stopped:
        return stopped
    root_test = _first_by_tier([c for c in by_dir.get("", []) if c.role == "test"], TEST_TIERS)

    def covers(stop: Detector) -> bool:
        if test_command:
            return True
        if root_test is None:
            return False
        # The root's own toolchain of that family (a uv workspace's
        # `uv run pytest`, hatch's `hatch test`) runs it from the root,
        # where no lockfile is written.
        return root_test.tier == "runner" or (
            root_test.tier == "toolchain" and root_test.family == stop.family
        )

    return {d: stop for d, stop in stopped.items() if not covers(stop)}


def _untested_root(
    by_dir: dict[str, list[Candidate]], matches: dict[str, list[_Match]]
) -> Detector | None:
    """A root that is a project (a Gemfile, a Makefile) with no test command
    found, while directories below it have theirs: a change to the root's
    own code would select only their scopes, and pass on them. Stopped like
    a `stop` detector's directory, saying which file made it a project."""
    if any(c.role == "test" for c in by_dir.get("", ())):
        return None
    tested = {d for d, cands in by_dir.items() if d and any(c.role == "test" for c in cands)}
    tops = sorted(d for d in tested if not any(a in tested for a in _ancestors(d) if a))
    project = next((m for m in matches.get("", ()) if not m.detector.stop), None)
    if project is None or not tops:
        return None
    shown = ", ".join(f"{d}/" for d in tops[:3]) + (" and more" if len(tops) > 3 else "")
    reason = (
        f"a project ({project.marker}) with no test command found: a change to it "
        f"would run only the tests in {shown}"
    )
    return project.detector.model_copy(update={"stop": True, "reason": reason})


def _corroborate(by_dir: dict[str, list[Candidate]], ci: list[Candidate]) -> None:
    """Fold `ci` into `by_dir`. CI running what a runner or toolchain already
    proposes is corroboration, said on that candidate, not a second one."""
    proposed: dict[tuple[str, str, str], Candidate] = {}
    for cands in by_dir.values():
        for o in cands:
            proposed.setdefault((o.dir, o.role, o.command), o)
    for c in ci:
        same = proposed.get((c.dir, c.role, c.command))
        if same is not None:
            same.corroborated = True
            if c.marker not in same.source:
                same.source += f"; CI runs it too ({c.marker})"
        else:
            by_dir.setdefault(c.dir, []).append(c)


#: How long one probe may take, start to end, in its own process.
_PROBE_TIMEOUT_S = 120
#: The most memory that process may take.
_PROBE_MEMORY = 2 << 30


def probe(
    root: Path,
    templates_dir: Path | None = None,
    *,
    test_command: str | None = None,
    timeout: float | None = None,
) -> Proposal:
    """`propose`, in a child process with a wall-clock limit (`timeout`,
    else `_PROBE_TIMEOUT_S`). What it reads is the repository's, whoever
    committed it: a file that sends a regex or a parser into the weeds holds
    the GIL, which no thread can time out, and would freeze the whole server
    and every connect queued behind it. A child that runs too long is
    killed, and one that fails for any reason is a `ConfigError` naming why,
    which every caller already answers.

    The child is given no more than it needs: `-P` keeps the working
    directory off its import path (a `fnmatch.py` there would run), it runs
    from `/`, its environment is `_CHILD_ENV` and not the server's secrets,
    and what it writes is capped at `_PROBE_OUTPUT`."""
    limit = _PROBE_TIMEOUT_S if timeout is None else timeout
    request = {"root": str(root), "templates_dir": templates_dir, "test_command": test_command}
    env = {k: v for k, v in os.environ.items() if k in _CHILD_ENV}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            done = subprocess.run(
                [sys.executable, "-P", "-c", "from kraft.detect import _child; _child()"],
                input=json.dumps(request, default=str).encode(),
                stdout=out,
                stderr=err,
                timeout=limit,
                env=env,
                cwd=os.sep,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigError(
                f"probing {root} took more than {limit:g}s; "
                "connect it with --test-command and --setup-command instead"
            ) from exc
        out.seek(0)
        raw = out.read(_PROBE_OUTPUT + 1)
        err.seek(0)
        said = err.read(64_000).decode("utf-8", "replace")
    if done.returncode != 0:
        lines = said.strip().splitlines()
        why = lines[-1] if lines else f"exit {done.returncode}"
        raise ConfigError(why if done.returncode == 2 else f"probing {root} failed: {why}")
    if len(raw) > _PROBE_OUTPUT:
        raise ConfigError(f"probing {root} answered more than {_PROBE_OUTPUT} bytes")
    return Proposal(**json.loads(raw))


#: What the probe's process inherits of the server's environment: git and
#: Python need these, and nothing else of the server's (its API keys, its
#: tokens) is any business of a process reading someone's repository.
_CHILD_ENV = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT"})
#: The most the probe's process may write, its answer included.
_PROBE_OUTPUT = 64 << 20


def _child() -> None:
    """`probe`'s other end: a request on stdin, the proposal on stdout, a
    `ConfigError` as exit 2 with its message on stderr."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (_PROBE_OUTPUT + 1, _PROBE_OUTPUT + 1))
        resource.setrlimit(resource.RLIMIT_AS, (_PROBE_MEMORY, _PROBE_MEMORY))
    except (ImportError, ValueError, OSError):
        pass  # no such limit here (Windows; macOS refuses RLIMIT_AS)
    request = json.loads(sys.stdin.read())
    templates = request["templates_dir"]
    try:
        proposal = propose(
            Path(request["root"]),
            load(Path(templates) if templates else None),
            test_command=request["test_command"],
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    json.dump(asdict(proposal), sys.stdout)


def propose(root: Path, table: Table, *, test_command: str | None = None) -> Proposal:
    """What to propose for the repository at `root`.

    `test_command`, when given, is the root's test command whatever the
    evidence says -- `""` for "the root has no tests" -- and directories
    below it are still probed (Kraft-k4mx).

    A setup recipe the root's own runner declares (`just setup`, `make
    setup`) is taken to prepare the whole repository: it is what a person
    runs after cloning, so a nested toolchain's install is not added beside it.
    """
    index = _Index(root)
    try:
        return _propose(index, table, test_command)
    finally:
        index.close()


def _propose(index: _Index, table: Table, test_command: str | None) -> Proposal:
    ci = _ci_candidates(index)
    devenv = _devcontainer_candidate(index)

    ignored = {n.casefold() for n in table.ignore_dirs}

    def eligible(d: str) -> bool:
        """Not hidden, and no part of it a usual non-project name, in any
        case: `Tests/`, `Benchmarks/`."""
        return not any(p.startswith(".") or p.casefold() in ignored for p in d.split("/"))

    dirs = {""} | {d for d in index.dirs if 0 < _depth(d) <= table.max_depth and eligible(d)}
    # A CI step's working directory is held to the same rules: vscode's ran
    # in src/vs/sessions/test/e2e.
    ci = [c for c in ci if c.dir in dirs]
    #: The CI files that run a test, or are named for it (fastapi's test.yml
    #: runs `bash scripts/test-cov.sh`): a setup line elsewhere is a bot's or
    #: a deploy's, whose flags are no guide to what the tests need.
    test_files = frozenset(c.marker for c in ci if c.role == "test") | frozenset(
        c.marker for c in ci if _CI_TEST_FILE.search(posixpath.basename(c.marker))
    )
    by_dir: dict[str, list[Candidate]] = {}
    matches: dict[str, list[_Match]] = {}
    for d in sorted(dirs, key=lambda x: (_depth(x), x)):
        by_dir[d], matches[d] = _detect(index, d, table)
    _in_env(index, by_dir, matches)
    _corroborate(by_dir, ci)
    if devenv is not None:
        by_dir[""].append(devenv)
    # The root stays even with no evidence of its own: a given test command
    # is its scope, whatever is found below it.
    by_dir = {d: cands for d, cands in by_dir.items() if cands or matches[d] or not d}
    stopped = _covered(_stop(by_dir, matches, table), by_dir, table, test_command)
    if not stopped and test_command is None:
        root = _untested_root(by_dir, matches)
        if root is not None:
            stopped[""] = root

    # A workspace root covers its family's members: their setup when its own
    # setup installs that family, their tests when its test runs that
    # family's (a pnpm root's `pnpm test` runs no Cargo member's tests).
    workspaces: dict[str, set[str]] = {
        d: {m.detector.family for m in ms if m.workspace and m.detector.family}
        for d, ms in matches.items()
    }
    #: Per scope, the families its test runs and its setup installs (None:
    #: any, a runner's task or a given command).
    runs: dict[str, set[str] | None] = {}
    installs: dict[str, set[str] | None] = {}

    def reach(a: str, f: str, of: dict[str, set[str] | None]) -> bool:
        return a in of and f in workspaces.get(a, ()) and (of[a] is None or f in of[a])

    venvs = {det.id: det.venv for det in table.detectors}
    scopes: list[Scope] = []
    claimed: set[str] = set()
    #: The root runner's file, when its setup recipe is the root's setup: a
    #: nested directory it names is prepared by it.
    recipe_text: str | None = None
    for d, cands in by_dir.items():
        # A member with a lockfile of its own installs on its own (a docs
        # site beside a pnpm workspace), so no workspace above covers it.
        locked = {m.detector.family for m in matches[d] if m.detector.lockfile}
        families = {m.detector.family for m in matches[d] if m.detector.family} - locked
        covering = {f for a in _ancestors(d) for f in families if reach(a, f, installs)}
        if any(a in claimed and a for a in _ancestors(d)):
            continue  # inside a directory that is already a scope of its own
        if any(reach(a, f, runs) for a in _ancestors(d) if a in claimed for f in families):
            continue  # its workspace root's test runs its tests
        parts: list[Candidate] = []
        if d == "" and test_command is not None:
            test = (
                Candidate("", "test", test_command, "runner", "given", "", "given")
                if test_command
                else None
            )
            parts = [test] if test else []
        elif stopped and test_command is None:
            test = None  # a suite Kraft cannot run would pass on the others'
        else:
            test, parts = _choose_test(cands)
        tested = _families(test, parts)
        setup = _setups([c for c in cands if c.role == "setup"], covering, tested, test_files)
        named = recipe_text is not None and _names_dir(recipe_text, d)
        if named:
            setup = [c for c in setup if c.tier == "runner"]
        venv = next((venvs[c.detector] for c in setup if venvs.get(c.detector)), None)
        if venv and test is not None and test.tier in ("runner", "ci"):
            _in_venv(test, venv)
        if test is None and d != "" and not workspaces.get(d):
            continue
        if test is None and d == "" and not setup:
            continue
        covered = not setup and (named or bool(covering))
        scopes.append(Scope(d, test, setup, covered, parts))
        if d == "" and [c.tier for c in setup] == ["runner"]:
            recipe_text = _recipe(index.text(setup[0].marker), setup[0].detector, setup[0].task)
        runs[d] = tested
        by_runner = [c.tier for c in setup] == ["runner"]
        installs[d] = None if by_runner else {c.family for c in setup if c.family}
        if test is not None:
            claimed.add(d)
        for c in (*parts, *setup):
            c.chosen = True

    tested = [s for s in scopes if s.test is not None]
    nested = [s for s in tested if s.dir]
    root_scope = next((s for s in tested if not s.dir), None)
    if not nested:
        test_scopes = [{"paths": ["**"], "command": root_scope.test.command}] if root_scope else []
    else:
        test_scopes = []
        if root_scope:
            test_scopes.append(
                {"paths": _unclaimed(index, "", claimed), "command": root_scope.test.command}
            )
        test_scopes += [
            {"paths": [f"{s.dir}/**"], "command": in_dir(s.dir, s.test.command, shell=False)}
            for s in nested
        ]
    shaped = [
        {"dir": s.dir, "test": s.test.command if s.test else None, "setup": s.setup_command}
        for s in scopes
    ]
    setup_command = combine_setup(shaped)
    if (
        setup_command is None
        and test_command == ""
        and not nested
        and not any(c.role == "setup" for cands in by_dir.values() for c in cands)
    ):
        # Said to have no tests, and nothing anywhere to prepare (a docs
        # repo): nothing is what it needs, rather than a second flag to say so.
        setup_command = ""
    return Proposal(
        test_command=test_scopes[0]["command"] if test_scopes else None,
        test_scopes=test_scopes,
        test_markers=[c.marker for s in tested for c in s.parts if c.marker],
        setup_command=setup_command,
        missing_setup=[s.dir or "." for s in tested if s.setup_command is None],
        scopes=shaped,
        candidates=[asdict(c) for cands in by_dir.values() for c in cands],
        ref=index.ref,
        stopped=[
            {"dir": d or ".", "reason": s.reason, "detector": s.id} for d, s in stopped.items()
        ],
        missing_tools=_missing_tools(scopes),
    )
