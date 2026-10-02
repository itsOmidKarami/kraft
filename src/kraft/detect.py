"""What `kraft repo connect` proposes as a repository's setup and test commands.

A proposal, never a decision: `config.probe_repo` hands it to the connect
route, which writes it into repos.yaml for a person to check, and nothing at
run time reads this module (`builtins.run_setup_command` runs what is
declared and infers nothing).

Three kinds of evidence, in the order a repository is most likely to mean them:

- **runner**: a task runner the repository wrote itself -- a justfile recipe,
  a Makefile target, a Taskfile or mise task, `script/test`. Whatever tool
  sits underneath, the repository already said how it wants to be driven.
- **ci**: the commands its CI runs (`.github/workflows`, `.gitlab-ci.yml` and
  the like). A test command is what CI runs, so CI beats a toolchain's
  default; a setup line from CI is shown and never chosen, since CI installs
  tools a worktree must not.
- **toolchain**: an ecosystem's default, picked by its lockfile, so a pnpm
  repository gets `pnpm`, a Poetry one `poetry`, and a pyproject.toml holding
  only ruff settings is not taken for a Python project.

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
import json
import os
import posixpath
import re
import shlex
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kraft.config import ConfigError, first_error, git_read, read_yaml

#: The packaged table. An operator's file of the same name under the
#: templates directory layers on top of it, never replaces it wholesale, so a
#: newer Kraft's detectors still reach an install whose file predates them.
PACKAGED = Path(__file__).with_name("detectors.yaml")
FILE = "detectors.yaml"

Tier = Literal["runner", "ci", "toolchain", "devenv"]
#: Which evidence wins, per role. CI setup is evidence only: CI installs
#: global tools (`npm i -g`, `pip install poetry`) a worktree must not.
TEST_TIERS: tuple[Tier, ...] = ("runner", "ci", "toolchain")
SETUP_TIERS: tuple[Tier, ...] = ("runner", "toolchain")

_ID = r"^[a-z][a-z0-9_-]*$"
_TEXT_LIMIT = 4_000_000


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
    """Holds when any `files` glob matches (or none is given) and every
    `contains` glob names some file its regex matches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    files: list[str] = []
    contains: dict[str, str] = {}

    @field_validator("contains")
    @classmethod
    def _patterns(cls, v: dict[str, str]) -> dict[str, str]:
        return _check_patterns(v)


class Command(BaseModel):
    """One proposal a detector can make. `tasks`: names to look for among the
    tasks the detector's reader finds, the first present substituted for
    `{task}`. `when`: any one of these conditions must hold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    tasks: list[str] = []
    when: list[Condition] = []


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


#: A partial clone's missing blob is fetched on demand; a probe must not
#: reach the network, let alone prompt for credentials.
_GIT_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


def _git(root: Path, *args: str) -> bytes:
    """A read the probe cannot do without: a failure is an error naming git's
    reason, never an empty answer that reads as an empty repository."""
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", *args],
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


def _blob(root: Path, oid: str) -> bytes:
    """At most `_TEXT_LIMIT` bytes of a blob, however large it is; empty when
    git cannot give it (a partial clone's missing blob)."""
    with subprocess.Popen(
        ["git", "--no-optional-locks", "cat-file", "blob", oid],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, **_GIT_ENV},
    ) as proc:
        raw = proc.stdout.read(_TEXT_LIMIT) if proc.stdout else b""
        proc.kill()
    return raw


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
        """`rel`'s content, at most `_TEXT_LIMIT` bytes of it, as text."""
        if rel not in self._text:
            if rel in self._blobs:
                raw = _blob(self.root, self._blobs[rel][0])
            elif rel in self.files:
                try:
                    with open(self.root / rel, "rb") as f:
                        raw = f.read(_TEXT_LIMIT)
                except OSError:
                    raw = b""
            else:
                raw = b""
            self._text[rel] = raw[:_TEXT_LIMIT].decode("utf-8", "replace")
        return self._text[rel]

    def executable(self, rel: str) -> bool:
        if rel in self._blobs:
            return self._blobs[rel][1]
        return rel in self.files and os.access(self.root / rel, os.X_OK)


def _join(d: str, rel: str) -> str:
    return f"{d}/{rel}" if d else rel


def _holds(index: _Index, d: str, cond: Condition) -> bool:
    if cond.files and not any(index.matching(d, g) for g in cond.files):
        return False
    return all(_contains(index, d, g, rx) for g, rx in cond.contains.items())


def _contains(index: _Index, d: str, glob: str, pattern: str) -> bool:
    rx = re.compile(pattern)
    return any(rx.search(index.text(_join(d, rel))) for rel in index.matching(d, glob))


# ── task readers ─────────────────────────────────────────────────────────────

_JUST_RECIPE = re.compile(r"^@?([A-Za-z_][\w-]*)(?:\s[^:\n]*)?:(?!=)", re.MULTILINE)
_MAKE_RULE = re.compile(r"^([^\s:#=][^:#=]*?)\s*::?(?!=)", re.MULTILINE)
_RAKE_TASK = re.compile(r"""\btask\s*\(?\s*:?["']?([\w:]+)""")
#: `npm init`'s placeholder: a `test` script that only ever fails.
_NPM_STUB = re.compile(r"no test specified", re.IGNORECASE)


def _first(index: _Index, d: str, names: Iterable[str]) -> str | None:
    return next((n for n in names if index.matching(d, n)), None)


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
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


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
        data = yaml.safe_load(index.text(_join(d, name))) or {}
    except yaml.YAMLError:
        return None
    return name, _keys(data.get("tasks") if isinstance(data, dict) else None), "task"


def _toml(index: _Index, rel: str) -> dict:
    try:
        return tomllib.loads(index.text(rel))
    except tomllib.TOMLDecodeError:
        return {}


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
    except ValueError:
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
    if "Rake::TestTask" in text:
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
                    found.append(
                        Candidate(
                            d,
                            role,
                            cmd.run,
                            det.tier,
                            _join(d, marker),
                            _join(d, marker),
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
#: Workflow files whose tests are not the repo's unit tests, read last.
_CI_LATE = re.compile(r"e2e|playwright|cypress|release|deploy|docs|nightly|publish|pages|bench")
#: Inputs to a step, not commands: `actions/github-script`'s `with: script:`
#: is JavaScript.
_CI_SKIP_KEYS = {"with", "env", "variables", "environment", "rules", "only", "except"}
_CI_SETUP_KEYS = {"before_script", "install", "before_install"}
#: A word that says a line runs tests. `check` and `verify` only behind the
#: runners that use them for that: `ruff check` and `cargo check` do not test.
_CI_TEST = re.compile(
    r"(?<![\w./-])(test|tests|pytest|rspec|jest|vitest|phpunit|ctest|tox|nox)(?![\w-])"
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


def _ci_lines(value: object) -> list[str]:
    if isinstance(value, str):
        return [line for line in value.replace("\\\n", " ").splitlines()]
    if isinstance(value, list):
        return [line for v in value for line in _ci_lines(v)]
    return []


def _ci_walk(node: object, wd: str, out: list[tuple[str, str, str]]) -> None:
    """(working directory, key, line) for every command line under `node`."""
    if isinstance(node, list):
        for v in node:
            _ci_walk(v, wd, out)
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
            _ci_walk(value, wd, out)  # CircleCI's long form: `run: {command: ...}`
        elif key in _CI_COMMAND_KEYS or key in _CI_SETUP_KEYS:
            out.extend((wd, str(key), line) for line in _ci_lines(value))
        else:
            _ci_walk(value, wd, out)


def _relative_dir(base: str, step: str) -> str | None:
    """`step` from `base`, relative to the repository root; None when it
    leaves the repository or cannot be known."""
    d = posixpath.normpath(_join(base, step) if base else step)
    if d == ".":
        return ""
    if d.startswith("..") or posixpath.isabs(d) or "$" in d:
        return None
    return d


def _ci_split(wd: str, line: str) -> list[tuple[str | None, str]]:
    """`cd frontend && npm test` -> [("frontend", "npm test")]: each `&&` part
    with the directory a `cd` before it moved to."""
    out: list[tuple[str | None, str]] = []
    here: str | None = wd
    for part in line.split("&&"):
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
    return out


#: `npm --prefix web ci`, `pnpm --dir web test`, `yarn --cwd web test`: a
#: package manager told which directory to run in.
_PREFIX = re.compile(
    r"^(?P<tool>npm|pnpm|yarn)\s+(?P<before>.*?)(?:--prefix|--dir|--cwd|-C)[=\s](?P<dir>\S+)(?P<after>.*)$"
)


def _shell_syntax(command: str) -> bool:
    """A variable, a pipe, a redirect, a `case` arm: a line that means
    something only inside the script around it, or only on CI."""
    if "$" in command or "`" in command:  # expanded inside double quotes too
        return True
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    return bool(re.search(r"[|;<>(){}\\]", bare))


def _strip_env(command: str) -> str:
    """`CI=true npm test` -> `npm test`: a test command runs without a shell,
    where a leading assignment is a program name."""
    words = command.split(" ")
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
        words.pop(0)
    return " ".join(words)


def _ci_candidates(index: _Index) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[tuple[str, str, str]] = set()
    for glob in _CI_FILES:
        for rel in index.matching("", glob):
            try:
                data = yaml.safe_load(index.text(rel))
            except yaml.YAMLError:
                continue
            lines: list[tuple[str, str, str]] = []
            _ci_walk(data, "", lines)
            for wd, key, raw in lines:
                base = _relative_dir("", wd) if wd else ""
                if base is None:
                    continue
                for d, part in _ci_split(base, raw.strip()):
                    if d is None:
                        continue
                    command = _strip_env(part.strip())
                    if not command or command.startswith("#") or _shell_syntax(command):
                        continue
                    head = command.split()[0]
                    if head in _CI_NOT_COMMANDS or "--version" in command or "--help" in command:
                        continue
                    if d and d not in index.children:
                        continue
                    if key in _CI_SETUP_KEYS or (
                        head in _CI_SETUP_TOOLS
                        and _CI_SETUP.search(command)
                        and not _CI_TEST.search(command)
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
    # The repo's own test workflow before its e2e, release or docs ones.
    return sorted(found, key=lambda c: bool(_CI_LATE.search(posixpath.basename(c.marker))))


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
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    keys = [k for k in _DEVCONTAINER_KEYS if k in data]
    parts = [c for k in keys for c in _devcontainer_command(data[k])]
    # A container's own provisioning is no worktree's to run.
    if not parts or any(re.search(r"\b(sudo|apt-get|apt|apk|yum|dnf)\b", p) for p in parts):
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


def _depth(d: str) -> int:
    return d.count("/") + 1 if d else 0


def _ancestors(d: str) -> list[str]:
    parts = d.split("/")
    return ["", *("/".join(parts[:i]) for i in range(1, len(parts)))] if d else []


def _first_by_tier(cands: list[Candidate], tiers: tuple[Tier, ...]) -> Candidate | None:
    """The first candidate by tier. Within CI: one a runner or toolchain also
    proposes first, then a line through a wrapper. A CI line running a bare
    tool (`pytest`, `jest`) comes after the toolchain: CI installed that tool
    on its own PATH, which a worktree's is not."""
    for tier in tiers:
        hit = next((c for c in cands if c.tier == tier and not c.bare), None)
        if tier == "ci":
            hit = next((c for c in cands if c.corroborated), hit)
        if hit is not None:
            return hit
    return next((c for c in cands if c.bare), None) if "ci" in tiers else None


def _setups(cands: list[Candidate], skip_families: set[str]) -> list[Candidate]:
    """A runner's setup recipe if there is one, else one toolchain setup per
    family (a Python and a JavaScript project at one root need both). A
    devcontainer's commands are never chosen: they are written for a
    container, and commonly install into its global interpreter."""
    runner = _first_by_tier(cands, ("runner",))
    if runner is not None:
        return [runner]
    picked: list[Candidate] = []
    families: set[str | None] = set()
    for c in cands:
        if c.tier == "toolchain" and c.family not in families and c.family not in skip_families:
            families.add(c.family)
            picked.append(c)
    return picked


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


def _corroborate(by_dir: dict[str, list[Candidate]], ci: list[Candidate]) -> None:
    """Fold `ci` into `by_dir`. CI running what a runner or toolchain already
    proposes is corroboration, said on that candidate, not a second one."""
    for c in ci:
        same = next(
            (o for o in by_dir.get(c.dir, ()) if (o.role, o.command) == (c.role, c.command)),
            None,
        )
        if same is not None:
            same.corroborated = True
            if c.marker not in same.source:
                same.source += f"; CI runs it too ({c.marker})"
        else:
            by_dir.setdefault(c.dir, []).append(c)


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
    ci = _ci_candidates(index)
    devenv = _devcontainer_candidate(index)

    def eligible(d: str) -> bool:
        parts = d.split("/")
        return not any(p.startswith(".") or p in table.ignore_dirs for p in parts)

    dirs = {""} | {d for d in index.dirs if 0 < _depth(d) <= table.max_depth and eligible(d)}
    dirs |= {c.dir for c in ci}
    by_dir: dict[str, list[Candidate]] = {}
    matches: dict[str, list[_Match]] = {}
    for d in sorted(dirs, key=lambda x: (_depth(x), x)):
        by_dir[d], matches[d] = _detect(index, d, table)
    _corroborate(by_dir, ci)
    if devenv is not None:
        by_dir[""].append(devenv)
    by_dir = {d: cands for d, cands in by_dir.items() if cands or matches[d]}

    # A workspace root covers its family's members: their setup always (the
    # root's install is what installs them), their tests when it has a test.
    workspaces: dict[str, set[str]] = {
        d: {m.detector.family for m in ms if m.workspace and m.detector.family}
        for d, ms in matches.items()
    }
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
        covering = {f for a in _ancestors(d) for f in workspaces.get(a, ())}
        if any(a in claimed and a for a in _ancestors(d)):
            continue  # inside a directory that is already a scope of its own
        tested_by = [
            a for a in _ancestors(d) if a in claimed and workspaces.get(a, set()) & families
        ]
        if tested_by:
            continue
        if d == "" and test_command is not None:
            test = (
                Candidate("", "test", test_command, "runner", "given", "", "given")
                if test_command
                else None
            )
        else:
            test = _first_by_tier([c for c in cands if c.role == "test"], TEST_TIERS)
        setup = _setups([c for c in cands if c.role == "setup"], covering & families)
        named = recipe_text is not None and _names_dir(recipe_text, d)
        if named:
            setup = [c for c in setup if c.tier == "runner"]
        if test is None and d != "" and not workspaces.get(d):
            continue
        if test is None and d == "" and not setup:
            continue
        covered = not setup and (named or bool(covering & families))
        scopes.append(Scope(d, test, setup, covered))
        if d == "" and [c.tier for c in setup] == ["runner"]:
            recipe_text = _recipe(index.text(setup[0].marker), setup[0].detector, setup[0].task)
        if test is not None:
            claimed.add(d)
        for c in (test, *setup):
            if c is not None:
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
    return Proposal(
        test_command=test_scopes[0]["command"] if test_scopes else None,
        test_scopes=test_scopes,
        test_markers=[s.test.marker for s in tested if s.test.marker],
        setup_command=combine_setup(shaped),
        missing_setup=[s.dir or "." for s in tested if s.setup_command is None],
        scopes=shaped,
        candidates=[asdict(c) for cands in by_dir.values() for c in cands],
        ref=index.ref,
    )
