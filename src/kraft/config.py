"""Reading and writing the YAML files the Settings screens edit.

`02` §4.7 (revised): the UI is an editor for files git tracks, not a front end
for a config table. Every write here lands in the same `templates/` directory an
operator edits by hand, so a change stays reviewable, diffable and revertible.

Writes are atomic — a temp file in the same directory, then `os.replace` — so a
crash mid-save can never leave a half-written policy the next start refuses to
load.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

import idna
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kraft.automated_review import AutomatedReview
from kraft.policy import PolicyError, SandboxPolicy, TemplatePolicyOverride, ToolNames, _cron_fields
from kraft.vocab import BY_VALUE
from kraft.worker import steering as _steering

if TYPE_CHECKING:
    from kraft.templates.environment import Workspace

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


#: The hand-rolled loaders said "'managed' must be a boolean"; pydantic says
#: "Input should be a valid boolean". Operators (and tests) know the former.
_SHAPE_PROSE = {
    "bool_type": "must be a boolean",
    "string_type": "must be a string",
    "list_type": "must be a list",
    "dict_type": "must be a mapping",
}


def first_error(exc: ValidationError, prefix: str) -> str:
    """One operator-facing line from a `ValidationError`, prefixed with the file.

    Every config loader here has always raised `ConfigError` with a single
    string, and seven call sites read exactly one message (three in
    `api/routes/gates.py`, three in `api/routes/work_items.py`, one in
    `templates.py`). Models raise a list; this is the adapter, so modelling the
    config does not become a rewrite of everything that reports on it.

    The field path is included because the current messages name the offending
    key, and an error that says only "value is not a valid boolean" is a
    regression an operator pays for.
    """
    err = exc.errors()[0]
    if not err["loc"]:
        # A model-level validator names what it refuses itself.
        return f"{prefix}: {err['msg'].removeprefix('Value error, ')}"
    head, *rest = (str(p) for p in err["loc"])
    where = f"'{head}'" + "".join(f".{p}" for p in rest)
    shape = _SHAPE_PROSE.get(err["type"])
    return f"{prefix}: {where} {shape}" if shape else f"{prefix}: {where}: {err['msg']}"


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


class YamlTooLarge(ValueError):
    """A YAML document past its node budget: refused before it is built."""


def bounded_yaml(text: str, budget: list[int]) -> object:
    """`yaml.safe_load`, for a file whoever committed it wrote. The node
    graph is composed (linear in the text: an alias is a shared node) and
    walked first, every use of an alias counting again, spending
    `budget[0]`; past it, `YamlTooLarge`, and nothing is constructed. A
    merge key over nested aliases (`<<: [*a, *a, ...]`) would otherwise
    build in time exponential in its depth, from a few hundred bytes."""
    loader = yaml.SafeLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except RecursionError:
            # Nesting a parser recurses through is refused like any other bomb.
            raise YamlTooLarge("YAML nested deeper than a repository's config goes") from None
        if node is None:
            return None
        stack = [node]
        while stack:
            budget[0] -= 1
            if budget[0] < 0:
                raise YamlTooLarge("more YAML nodes than a repository's config holds")
            n = stack.pop()
            if isinstance(n, yaml.MappingNode):
                for key, value in n.value:
                    stack += (key, value)
            elif isinstance(n, yaml.SequenceNode):
                stack.extend(n.value)
        return loader.construct_document(node)
    finally:
        loader.dispose()


def _small_text(path: Path, cap: int = 1 << 20) -> str | None:
    """A repository's own file, read from its working copy: a regular file
    only (not a symlink to a FIFO, whose read would block forever), of at
    most `cap` bytes. None otherwise: the facts a probe reports from it are
    never a reason to fail."""
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            return None
        with open(path, "rb") as f:
            return f.read(cap).decode("utf-8")
    except (OSError, ValueError):  # not UTF-8 is unreadable too
        return None


def _small_yaml(path: Path, cap: int = 1 << 20) -> dict:
    """`_small_text`'s YAML, bounded as `bounded_yaml` bounds it; empty when
    it is not a small regular file or not a mapping."""
    text = _small_text(path, cap)
    try:
        data = bounded_yaml(text, [20_000]) if text is not None else None
    except (ValueError, RecursionError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def write_text(path: str | Path, text: str) -> None:
    """Write `text`, atomically. A reader sees the old file or the new one.

    Every config Kraft owns is read straight off disk by something that did not
    write it -- a launch, an intake, the next load -- so a
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


class TestScope(BaseModel):
    """One `test_scopes` entry. Not asserted as a pytest class."""

    __test__ = False
    model_config = ConfigDict(strict=True, extra="allow")

    paths: list[str] = Field(min_length=1)
    command: str = Field(min_length=1)

    # Deliberately does *not* synthesize a `["**"]` scope from a legacy
    # `test_command`: that synthesis used to be written into the loaded entry,
    # and any route that then re-saved repos.yaml (`PATCH`/`DELETE /repos`)
    # persisted it -- baking in whatever `test_command` was current at load
    # time (Kraft-9wzy). Callers that need the wrap do it at the point of use.


#: Keys an older `repos.yaml` entry may still carry, and where each went.
#: `repos.yaml` keys 2.0 renamed, old name to new: read under the old name,
#: with a warning naming the new one, and written back under the new name at
#: the next save.
_RENAMED_KEYS = {"default_chain_template": "default_chain"}
#: `(path, old key)` already warned about: the file is re-read on every
#: request, and one line per process is what an operator needs.
_RENAME_WARNED: set[tuple[object, str]] = set()

#: Keys an older `repos.yaml` entry may still carry that nothing reads since
#: 2.0, and what replaced each: the loader's warning and `kraft admin
#: doctor`'s `keys` row say it instead of "remove it".
RETIRED_REPO_KEYS = {
    "default_model": "set a model per harness profile under 'models:', then remove it",
    "default_root_merge_policy": "a workspace's 'root_pointer_default' replaces it; remove it",
    "submodules": "connect each submodule as a repos.yaml entry of its own "
    "(`kraft repo connect` in its directory), then remove it",
    "allow_cross_repo": "nothing replaces it; remove it",
}

#: A harness profile id, the key of `RepoEntry.models`: the same rule as
#: `kraft.templates.environment.Identifier`, the id a task's `harness:` names.
_PROFILE_ID = r"^[a-z][a-z0-9_-]*$"


class RepoEntry(BaseModel):
    """One `repos.yaml` entry. Strict, so `managed: "true"` is rejected as the
    hand-rolled loader rejected it; unknown keys ride along (`extra="allow"`)
    because entries carry keys this loader never read (`name`, `enabled`,
    `default_chain`, ...) and a re-save must not drop them."""

    model_config = ConfigDict(strict=True, extra="allow")

    path: str = Field(min_length=1)
    #: The repository id a workspace names this entry by (`workspaces:`
    #: below the list). Optional: only a workspace's root and members need
    #: one, and `kraft repo connect` writes it for them.
    id: Annotated[str, Field(pattern=_PROFILE_ID)] | None = None
    #: A display name; None falls back to `path` wherever this entry is shown.
    name: str | None = None
    #: Which chain a new item against this repo runs, when the item doesn't
    #: say. None falls back to `"default"`. `default_chain_template` before 2.0.
    default_chain: str | None = None
    forge: str | None = None
    project: str | None = None
    # True, not False: every entry that predates this field was connected by a
    # human, and `managed` is what keeps a human-connected repo out of
    # Settings' "Detected" section. Auto-connected children are written with an
    # explicit `managed: false` instead of relying on a default.
    managed: bool = True
    #: The model an agent task launches with in this repository, per harness
    #: profile id (Ruling 165): under the task's own `model:` and the item's
    #: override, over the profile's `defaults:`. Keyed by profile because one
    #: repo-wide `default_model` was handed to every provider alike.
    models: dict[Annotated[str, Field(pattern=_PROFILE_ID)], str] = {}
    # The command CI runs for this repo -- what the changed-test-scope
    # verification runs as one `**` scope. One command for every repo is what
    # let verify and CI drift apart (Kraft-579). None, with no `test_scopes`,
    # leaves verification nothing to run, and it stops for a human.
    test_command: str | None = None
    test_scopes: list[TestScope] | None = Field(default=None, min_length=1)
    #: Where this repository states its intended behaviour, relative to its
    #: root (intent-process design §3). None: the repository does no
    #: intent-driven development, and no agent is told of a tree.
    intent_dir: str | None = Field(default=None, min_length=1)
    #: Path-scoped execution contexts inside this repository, in the V1
    #: `Area` shape (`repository-area-can-declare-setup-and-test-scopes`):
    #: each area's test scopes join the repository's, and its `setup` runs
    #: before any of them does. Never forge targets.
    areas: dict[str, dict] = {}
    # Files `git worktree add` cannot carry: it checks out tracked content at
    # HEAD, so an untracked `.python-version` never reaches the worktree
    # (Kraft-gxcmy). Relative file paths only: a directory here is how a list
    # like this starts dragging `.venv` and `node_modules` into every worktree,
    # and a glob is the same trap with extra steps.
    local_files: list[Annotated[str, Field(min_length=1)]] = []
    # How this repo's worktree is prepared. No default and no fallback: an
    # absent key is "nobody has decided yet" and stops the chain when the
    # worktree is built, while `""` is a deliberate "nothing to do"
    # (Kraft-kji8w). Validated for shape here; required at use, because a
    # ConfigError raised at load would take down every repo at once.
    setup_command: str | None = None
    # Layered onto the worker baseline, which is an allowlist rather than the
    # daemon's inherited environment (Kraft-69atv).
    env: dict[str, str] = {}
    env_passthrough: list[Annotated[str, Field(min_length=1)]] = []
    deny_tools: ToolNames = []
    steering: list[str] = []
    #: Where this repository's tasks run (`kraft.worker.backends`). `false` is
    #: an explicit "none", kept as written so a re-save round-trips.
    sandbox: SandboxPolicy | Literal[False] | None = None
    #: The repository policy layer (`repository-policy-cannot-relax-instance-
    #: safety`): applied after the instance policy and before everything a
    #: work item or chain adds, and allowed only to tighten what it inherits.
    #: The entry's own `deny_tools` and `sandbox` above belong to the same layer
    #: (Ruling 105) and are folded into it by `repository_override`.
    policy: TemplatePolicyOverride | None = None
    #: The automated reviewer `mr.automated_review` waits for (Ruling 171).
    automated_review: AutomatedReview | None = None
    #: `false`: this repository runs no CI, so both CI waits, `mr.ci` before
    #: the merge and `mr.post_merge_ci` after it, pass at once instead of
    #: waiting on checks that never come (Kraft-9efnk.12). Opt-in: an absent
    #: key waits, as before.
    ci_checks: bool = True
    #: An absent key is enabled (Ruling 212): every existing entry that
    #: predates this field connected a repo a human meant to run against.
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _read_renamed_keys(cls, data: Any) -> Any:
        """Read a key under its pre-2.0 name: `config/` is seeded once and
        never overwritten, so a rename lives in the reader rather than in a
        migration that rewrites a user's file. The next save writes the new
        name; a file naming both keeps the new one. A pre-forge
        `gitlab_project` is read as `forge: gitlab` and `project:`."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        legacy = data.pop("gitlab_project", None)
        # Both `forge` and `project` must be absent: a hand-edited
        # half-migrated entry carrying an explicit `project` beside the legacy
        # key keeps its own. The next save writes the new shape.
        if data.get("forge") is None and data.get("project") is None and legacy:
            data["forge"] = "gitlab"
            data["project"] = legacy
        for old, new in _RENAMED_KEYS.items():
            if old in data:
                value = data.pop(old)
                kept = data.get(new) is None
                if kept:
                    data[new] = value
                if (data.get("path"), old) not in _RENAME_WARNED:
                    _RENAME_WARNED.add((data.get("path"), old))
                    logger.warning(
                        "repos.yaml: %s: %r is %r since 2.0; %s",
                        data.get("path"),
                        old,
                        new,
                        "read as it, and written under the new name at the next save"
                        if kept
                        else "the entry names both, so the new one is read",
                    )
        return data

    @field_validator("areas")
    @classmethod
    def _typed_areas(cls, v: dict[str, dict]) -> dict[str, dict]:
        """Checked as V1 `Area`s, kept as written so a re-save round-trips."""
        from kraft.templates.environment import Area, Identifier  # `kraft.templates` imports us

        TypeAdapter(dict[Identifier, Area]).validate_python(v)
        return v

    @field_validator("local_files")
    @classmethod
    def _safe_local_files(cls, v: list[str]) -> list[str]:
        for rel in v:
            if rel.endswith("/"):
                raise ValueError(f"entry {rel!r} must name a file")
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise ValueError(f"entry {rel!r} must be a relative path inside the repo")
            if any(c in rel for c in "*?["):
                raise ValueError(f"entry {rel!r} must be a literal path, not a glob")
        return v

    @field_validator("intent_dir")
    @classmethod
    def _intent_dir_inside_repo(cls, v: str | None) -> str | None:
        if v is not None and (Path(v).is_absolute() or ".." in Path(v).parts):
            raise ValueError(f"intent_dir {v!r} must be a relative path inside the repo")
        return v

    @field_serializer("policy")
    def _policy_as_written(self, v: TemplatePolicyOverride | None) -> dict | None:
        """Only the fields the operator set: Settings re-saves what
        `load_repos` returns, and a block of `null`s is not what they wrote."""
        return v.model_dump(exclude_none=True) if v is not None else None

    @model_validator(mode="after")
    def _one_sandbox(self) -> RepoEntry:
        """Two sandboxes for one repository cannot both hold; which one
        silently won would be a guess."""
        if self.sandbox and self.policy is not None and self.policy.sandbox is not None:
            raise ValueError(
                f"repos.yaml: {self.path}: set 'sandbox' or 'policy.sandbox', not both"
            )
        return self

    @field_validator("sandbox", mode="before")
    @classmethod
    def _typed_sandbox(cls, v: Any, info: ValidationInfo) -> Any:
        """Typed here, so a refusal names the entry and says what is wrong in
        `SandboxPolicy`'s own words rather than as a three-way union error."""
        if v is None or v is False:
            return v
        try:
            return SandboxPolicy.model_validate(v)
        except ValidationError as exc:
            where = f"repos.yaml: {info.data.get('path')}: sandbox"
            raise ValueError(first_error(exc, where)) from exc

    @property
    def effective_sandbox(self) -> SandboxPolicy | None:
        """The sandbox this entry sets, under either key (`_one_sandbox`
        refuses both), or None: `false` sets none."""
        return self.sandbox or (self.policy.sandbox if self.policy is not None else None)

    @model_validator(mode="after")
    def _no_unrecognised_key_passes_silently(self, info: ValidationInfo) -> RepoEntry:
        """`extra="allow"` keeps keys Kraft writes but does not type, and a
        retired key an older install still carries. None may pass silently
        (Kraft-4hn34): a key one typo away from a real field is refused,
        naming the field -- `automated_reviews:` would otherwise read as "no
        reviewer configured" -- and any other is a warning naming it, unless
        the caller reports them itself (`kraft admin doctor` fails a row)."""
        quiet = (info.context or {}).get("unrecognised_keys_reported", False)
        for key in unrecognised_repo_keys(self.model_extra or {}):
            meant = _near_miss(key)
            if meant is not None:
                raise ValueError(
                    f"repos.yaml: {self.path}: unknown key {key!r}: did you mean {meant!r}?"
                )
            if not quiet and (self.path, key) not in _RENAME_WARNED:
                _RENAME_WARNED.add((self.path, key))
                logger.warning(
                    "repos.yaml: %s: %r binds nothing; %s",
                    self.path,
                    key,
                    RETIRED_REPO_KEYS.get(key, "remove it"),
                )
        return self

    @model_validator(mode="after")
    def _steering_exists(self, info: ValidationInfo) -> RepoEntry:
        # The context carries the library's steering profiles (name to
        # instructions) only when the caller wants the names checked
        # (`load_repos(steering=...)`): a repository saved from Settings.
        profiles = (info.context or {}).get("steering")
        if profiles is not None:
            try:
                _steering.select(self.steering, profiles, where=f"repos.yaml: {self.path}")
            except _steering.SteeringError as exc:
                raise ValueError(str(exc)) from exc
        return self

    def repository_override(self) -> TemplatePolicyOverride | None:
        """This entry's repository policy layer, or None when it restricts
        nothing: its `policy:` block with the entry's own `deny_tools` and
        `sandbox` folded in (Ruling 105), so everything downstream reads one
        policy rather than a policy and two stray keys."""
        block = self.policy.model_dump(exclude_none=True) if self.policy is not None else {}
        deny = [*(block.get("deny_tools") or ()), *self.deny_tools]
        if deny:
            block["deny_tools"] = list(dict.fromkeys(deny))
        if self.sandbox:
            block["sandbox"] = self.sandbox
        return TemplatePolicyOverride.model_validate(block) if block else None

    #: `enabled`, `name` and `default_chain` carry a typed default
    #: so every reader can use the attribute, but an entry that never set one
    #: must not have it reappear at its default the next time this entry is
    #: written out (a save, or `GET /repos`) -- an absent `enabled` means
    #: enabled (Ruling 212), not "enabled, and now written down as such".
    _DEFAULTED_ON_ABSENCE: ClassVar[tuple[str, ...]] = (
        "enabled",
        "name",
        "default_chain",
    )

    def model_dump_repo(self, **kwargs: Any) -> dict:
        """`model_dump`, keeping a `_DEFAULTED_ON_ABSENCE` field absent when
        this entry never set it, rather than filling in its default."""
        data = self.model_dump(**kwargs)
        for key in self._DEFAULTED_ON_ABSENCE:
            if key not in self.model_fields_set:
                data.pop(key, None)
        return data


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance; the stdlib has only similarity ratios."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def unrecognised_repo_keys(entry: dict) -> list[str]:
    """The keys of one `repos.yaml` entry that nothing in Kraft reads
    (Kraft-4hn34). The loader warns about each one and `kraft admin doctor`
    fails on them; one definition serves both."""
    known = set(RepoEntry.model_fields)
    return sorted(k for k in entry if k not in known)


def _near_miss(key: str) -> str | None:
    """The field `key` is most likely a typo of: within edit distance 2."""
    distance, field = min((_edit_distance(key, f), f) for f in RepoEntry.model_fields)
    return field if distance <= 2 else None


def load_repos(path: str | Path, *, steering: Mapping[str, str] | None = None) -> list[RepoEntry]:
    """Parse `repos.yaml` into its entries, or raise `ConfigError`.

    `steering` is the library's steering profiles, name to instructions. When
    given, every entry's `steering:` names must be among them and fit the
    budget: the repository save passes it, so a name the library does not
    define is refused there. Every reader leaves it off: a profile removed
    after the fact must not 422 the very screens an operator would use to fix
    it. Intake resolves the names again, and refuses an item whose
    repository names a profile the library no longer has.
    """
    path = Path(path)
    data = read_yaml(path, REPOS_DEFAULT)
    if "repositories" in data:
        # Loud, not ignored: a file keyed this way would otherwise load no
        # repository at all, and only a workspace naming one would ever say so
        # -- blaming the workspace (Kraft-iep21, Ruling 177).
        raise ConfigError(
            "repos.yaml: top-level 'repositories:' is not read; did you mean 'repos:'? "
            "Kraft reads a list of entries under 'repos:', each naming its 'path'"
        )
    repos = data.get("repos") or []
    if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
        raise ConfigError("repos.yaml: 'repos' must be a list of mappings")
    ctx = {"steering": steering} if steering is not None else None
    out: list[RepoEntry] = []
    for r in repos:
        try:
            out.append(RepoEntry.model_validate(r, context=ctx))
        except ValidationError as exc:
            err = exc.errors()[0]
            # A custom validator's message is already a whole sentence naming
            # the file (`_typed_sandbox`, `_steering_exists`); do not wrap it twice.
            msg = err["msg"].removeprefix("Value error, ")
            if msg.startswith("repos.yaml"):
                raise ConfigError(msg) from exc
            raise ConfigError(first_error(exc, "repos.yaml")) from exc
    ids = [r.id for r in out if r.id]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise ConfigError(f"repos.yaml: repository id(s) {twice} name more than one entry")
    return out


def load_workspaces(path: str | Path) -> dict[str, Workspace]:
    """`repos.yaml`'s `workspaces:`, in the V1 shape: a root repository and
    each member mounted at its path, both naming a connected entry by `id`
    (`workspace-declares-root-and-members`). Both ends of every reference are
    resolved here, when the file is read: a workspace naming no connected
    repository would otherwise assemble an empty checkout hours later."""
    # Here, not at the top: `kraft.templates` imports this module.
    from kraft.templates.environment import Workspace

    repos = load_repos(path)
    ids = {r.id for r in repos if r.id}
    raw = read_yaml(path, REPOS_DEFAULT).get("workspaces") or {}
    if not isinstance(raw, dict):
        raise ConfigError("repos.yaml: 'workspaces' must be a mapping keyed by workspace id")
    out: dict[str, Workspace] = {}
    for ws_id, body in raw.items():
        try:
            ws = Workspace.model_validate({"id": ws_id, **(body or {})})
        except ValidationError as exc:
            raise ConfigError(first_error(exc, f"repos.yaml: workspaces.{ws_id}")) from exc
        if ws.root not in ids:
            raise ConfigError(
                f"repos.yaml: workspaces.{ws_id}: root {ws.root!r} is no connected repository id"
            )
        for name, member in ws.members.items():
            if member.repository not in ids:
                raise ConfigError(
                    f"repos.yaml: workspaces.{ws_id}.members.{name}: {member.repository!r} "
                    "is no connected repository id"
                )
        out[ws_id] = ws
    return out


def save_repos(path: str | Path, repos: list[dict], workspaces: dict | None = None) -> None:
    """Write the repository list. `workspaces` None keeps the file's own
    `workspaces:` section, so a Settings save of one entry never drops them."""
    if workspaces is None:
        workspaces = read_yaml(path, REPOS_DEFAULT).get("workspaces")
    write_yaml(path, {"repos": repos, **({"workspaces": workspaces} if workspaces else {})})


def git_read(
    cwd: Path,
    *args: str,
    expected_failure: bool = False,
    strip: bool = True,
    errors: str = "replace",
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

    Output is decoded as UTF-8 with `errors`: `replace` by default, so a file
    or a path that is not UTF-8 (a Latin-1 source file, a binary blob) reads
    with `U+FFFD` in place of what cannot be decoded instead of raising --
    a `UnicodeDecodeError` escaped this function and failed the whole diff
    route. `surrogateescape` for a caller that writes or hashes the bytes
    back out (`.encode("utf-8", "surrogateescape")` gives them back exactly).
    """
    # `core.fsmonitor` names a program git runs on a status-like read: from
    # a repository's own .git/config, that is the repository running code.
    cmd = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args]
    try:
        out = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            encoding="utf-8",
            errors=errors,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git_read could not run %s in %s: %s", cmd, cwd, exc)
        return None
    if out.returncode != 0:
        log = logger.debug if expected_failure else logger.warning
        log("git_read %s in %s failed: %s", cmd, cwd, out.stderr.strip())
        return None
    return out.stdout.strip() if strip else out.stdout


#: Roots Kraft itself writes into and never means to commit. `.engineering/`
#: is session notes and gate artifacts (`agent.py:artifact_path`); `docs/
#: superpowers/` is the legacy convention the same content used to live under
#: (CLAUDE.md) -- both gitignored on `main`, both still landing in a spec/plan
#: attachment a stale worktree copies in (Kraft-vu26). An untracked path under
#: one is Kraft's own: `forge.git` keeps it out of every commit and
#: `review.read_change` out of every diff (Kraft-tugdf.22).
KRAFT_ROOTS = (".engineering", "docs/superpowers")


@contextmanager
def base_ignore_args(repo: Path, base: str) -> Iterator[list[str]]:
    """`-c core.excludesFile=<scratch>`, naming a temp file holding `origin/
    <base>`'s current `.gitignore` -- `base` being the item's base branch
    (`builtins.base_branch`) -- or `[]` if there is no `origin/<base>`, no
    `.gitignore` there, or git refuses to say.

    Layered on top of whatever `.gitignore` is actually checked out in
    `repo`, not a replacement for it -- `core.excludesFile` is git's own
    mechanism for an extra, untracked set of ignore rules, so this only ever
    widens what a `git status`/`git add` in `repo` treats as ignored, never
    narrows it.

    A worktree's checked-out `.gitignore` is whatever the base looked like
    when `ensure_worktree` cut the worktree, and nothing refreshes it afterward
    short of a full rebase, which most nodes never trigger. A rule the base
    gains later (Kraft-vu26: `.engineering/` widened past `sessions/`, then
    `docs/superpowers/` added) is invisible to that worktree's `git status`/
    `git add` until then, so whatever a node writes to the now-ignored path
    stages and commits exactly as if the rule had never landed, and rides
    into the merge request -- caught live on work item 46ef3286, whose
    worktree predated the `docs/superpowers/` rule by under an hour. Reading
    the base's own copy from its remote-tracking ref sidesteps the lag
    outright: it does not matter how old the branch's checkout is. The base's,
    not a hardcoded `main`'s: the merge request lands on the base, so its
    rules are the ones the change must satisfy (Kraft-v9gbi).
    """
    # Byte for byte: a pattern naming a path that is not UTF-8 still matches it.
    content = git_read(
        repo,
        "show",
        f"origin/{base}:.gitignore",
        expected_failure=True,
        strip=False,
        errors="surrogateescape",
    )
    if not content:
        yield []
        return
    with tempfile.NamedTemporaryFile(
        "w",
        prefix="kraft-base-gitignore-",
        suffix=".txt",
        encoding="utf-8",
        errors="surrogateescape",
    ) as f:
        f.write(content)
        f.flush()
        yield ["-c", f"core.excludesFile={f.name}"]


#: Forges Kraft has an adapter for, by what an origin's host says. A
#: self-hosted instance counts when its host names the forge
#: (`gitlab.example.com`, `github.example.com`); any other host is set by hand
#: in repos.yaml.
_FORGES = ("gitlab", "github")
#: A URL-style remote (`https://`, `ssh://`, which may carry a port), or an
#: scp-style one (`git@host:group/repo`, where what follows the colon is
#: always the path: GitLab's numeric group ids included).
_REMOTE = (
    re.compile(
        r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$", re.IGNORECASE
    ),
    re.compile(r"^(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>[^/].*)$"),
)


def _detect_forge(remote: str) -> tuple[str | None, str | None]:
    """(forge, project) from an origin URL, or (None, None). Never raises.

    Read from the URL's host, not anywhere in the string: `github.com` in a
    path, or a host like `notgithub.company.io`, says nothing about the forge.
    """
    match = next((m for rx in _REMOTE if (m := rx.match(remote.strip()))), None)
    if match is None:
        return None, None
    labels = match["host"].lower().split(".")
    forge = next((f for f in _FORGES if f in labels or f"{f}.com" == match["host"].lower()), None)
    if forge is None:
        return None, None
    return forge, (match["path"].strip("/").removesuffix(".git") or None)


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


def repository_identity(path: str | Path) -> tuple[str, str]:
    """`path` resolved, and its origin's URL as `same_repository` compares
    them: a local origin resolved like a path (its `.git` directory is the
    repository), a remote one without a trailing `/` or `.git`, empty with
    no origin."""
    path = Path(path).resolve()
    url = (git_read(path, "remote", "get-url", "origin", expected_failure=True) or "").rstrip("/")
    if url.startswith(("/", "file://")):
        local = Path(url.removeprefix("file://")).resolve()
        url = str(local.parent if local.name == ".git" else local)
    else:
        url = url.removesuffix(".git")
    return url, str(path)


def same_repository(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Whether two `repository_identity`s are two checkouts of one repository:
    `b` is `a`'s origin, or both share an origin. A root's submodule
    checkout and the member connected on its own are the usual pair."""
    (origin_a, path_a), (origin_b, path_b) = a, b
    if path_a == path_b:
        return False
    return bool(origin_a) and origin_a in (origin_b, path_b)


#: A partial clone's missing blob is fetched on demand; a read of a
#: repository's files must not reach the network, let alone prompt for
#: credentials.
GIT_READ_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


def _submodule_paths(gitmodules: str) -> list[str]:
    """Every `submodule.<name>.path` in a `.gitmodules`'s text, as git reads
    it; empty when git cannot.

    Parsed by git, not by Python: the file is the repository's to write, and
    `configparser` took seconds to hours on a crafted one. Its option regex
    backtracks quadratically on a long run of spaces, holding the GIL, so one
    line froze the whole server; its `%(name)s` interpolation expands a few
    hundred bytes to gigabytes. git's own parser is linear and expands
    nothing. It reads the text from stdin, with no repository around it, so
    no `.git/config` is read. `--no-includes`: git follows `[include]` and
    `[includeIf]` in a config read from stdin, which let the repository
    have the server open any path, a FIFO among them."""
    try:
        done = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "config",
                "--no-includes",
                "--file",
                "-",
                "--null",
                "--get-regexp",
                r"^submodule\..*\.path$",
            ],
            input=gitmodules.encode("utf-8"),
            cwd="/",
            capture_output=True,
            timeout=10,
            check=False,
            env={**os.environ, **GIT_READ_ENV},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:  # 1 is no submodule at all, 128 a file git cannot parse
        return []
    paths = set()
    for entry in done.stdout.decode("utf-8", "replace").split("\0"):
        _key, newline, path = entry.partition("\n")
        if newline and path:
            paths.add(path)
    return sorted(paths)


def probe_repo(
    path: str | Path,
    *,
    test_command: str | None = None,
    templates_dir: Path | None = None,
    detect: bool = True,
    timeout: float | None = None,
) -> dict:
    """What Kraft can tell about a candidate repo without changing anything.

    Read-only on purpose (design 5a): "Kraft flags it but does not change repo
    files". Everything is best-effort — a field it cannot determine comes back
    None or empty rather than failing the probe. The one exception is a broken
    `detectors.yaml` under `templates_dir`, which raises `ConfigError`.

    The setup and test commands are `kraft.detect`'s proposal: the detector
    table packaged with Kraft, with `templates_dir`'s `detectors.yaml` on top.
    `test_command`, when given, takes the root's proposed test command's place
    (`""`: the root has no tests), and nested scopes are still probed.

    `detect=False` stops at the facts that need no detector table -- the
    repository's path, name, branch, submodules, beads, forge -- which is all
    a caller resolving a path or checking "already connected" needs, and
    cannot be broken by an operator's `detectors.yaml`.
    """
    from kraft import detect as detect_mod  # `kraft.detect` imports this module

    p = Path(path).expanduser()
    if not p.is_dir():
        raise ConfigError(f"{p} is not a directory")
    root = normalized_repo_root(p)
    if root is None:
        raise ConfigError(f"{p} is not a git repository")

    gitmodules = _small_text(root / ".gitmodules")
    submodules = _submodule_paths(gitmodules) if gitmodules is not None else []

    beads = root / ".beads"
    # Parsed here, in the server, so held to what a beads config needs: a
    # megabyte of YAML is seconds of the server's CPU on every connect.
    beads_config = _small_yaml(beads / "config.yaml", 64 << 10) if beads.is_dir() else {}
    export = beads_config.get("export") or {}

    remote = git_read(root, "remote", "get-url", "origin", expected_failure=True) or ""
    forge, project = _detect_forge(remote)

    facts = {
        "path": str(root),
        "name": root.name,
        "branch": git_read(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "submodules": submodules,
        "has_beads": beads.is_dir(),
        "beads_export_auto": bool(export.get("auto")),
        "beads_export_git_add": bool(export.get("git-add")),
        "has_engineering": (root / ".engineering").is_dir(),
        "forge": forge,
        "project": project,
    }
    if not detect:
        return facts
    proposal = detect_mod.probe(root, templates_dir, test_command=test_command, timeout=timeout)
    return {
        **facts,
        "test_command": proposal.test_command,
        "test_scopes": proposal.test_scopes,
        "test_markers": proposal.test_markers,  # what each command was read from (Kraft-enc5z)
        "setup_command": proposal.setup_command,
        # Told, not stored: which directories have tests and no setup, each
        # directory's own commands, every command the evidence supports, and
        # the commit they were read from.
        "missing_setup": proposal.missing_setup,
        "scopes": proposal.scopes,
        "candidates": proposal.candidates,
        "read_from": proposal.ref,
        "stopped": proposal.stopped,
        "missing_tools": proposal.missing_tools,
    }


# ── access ───────────────────────────────────────────────────────────────────


class _Model(BaseModel):
    """One settings file under `templates/`, read and written only through its
    model. `extra="forbid"`: a key nobody reads is a typo an operator wants
    told about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The file's name in an error message.
    FILE: ClassVar[str]

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """The file at `path`, every missing key defaulted. A missing file is
        all defaults. Raises `ConfigError` naming the file."""
        try:
            return cls.model_validate(read_yaml(path))
        except ValidationError as exc:
            raise ConfigError(first_error(exc, cls.FILE)) from exc

    def save(self, path: str | Path) -> None:
        """Write every field, atomically. `write_yaml` stages through
        `mkstemp`, which creates at 0600, and `os.replace` carries that mode
        onto the target -- which is what `notify.yaml`'s token relies on."""
        write_yaml(path, self.model_dump())


def host_name(host: str) -> str | None:
    """The name in an HTTP `Host` header, in the one form `allowed_hosts` is
    kept in: lowercased, without its port or a trailing dot, an IPv6 literal
    in brackets. None for one that does not parse (an unclosed IPv6 bracket)
    or carries userinfo (`evil@127.0.0.1`, which `urlsplit` would read as
    127.0.0.1), which no list holds."""
    if "@" in host:
        return None
    try:
        name = urlsplit(f"//{host}").hostname
    except ValueError:
        return None
    name = (name or "").removesuffix(".")
    if not name:
        return None
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name
    return f"[{ip.compressed}]" if ip.version == 6 else ip.compressed


def url_host(host: str) -> str:
    """`host` as it goes into a URL: an IPv6 literal in brackets, since in
    `http://::1:8765` the port cannot be told from the address. Anything else
    as it is."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


#: A host name or a bracketed IPv6 literal, once `host_name` has lowercased it.
_HOST_NAME = re.compile(
    r"\[[0-9a-f:.%]+\]|[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?)*"
)


#: A label the WHATWG URL parser reads as a number: decimal, or `0x` hex.
_NUMBER_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")


def _numeric_ipv4(name: str) -> str | None:
    """`name` as the dotted quad a browser reads it as, when its last label
    is a number: `127.1`, `0x7f.1` and `2130706433` are all 127.0.0.1 to the
    WHATWG URL parser, and that is the Host it sends. None for any other
    name; ValueError for one in that form that is no IPv4 address, which a
    browser refuses to open."""
    labels = name.split(".")
    if not _NUMBER_LABEL.fullmatch(labels[-1]):
        return None
    if len(labels) > 4:
        raise ValueError(name)
    numbers = []
    for label in labels:
        if label.startswith("0x"):
            numbers.append(int(label[2:] or "0", 16))
        elif len(label) > 1 and label.startswith("0"):
            numbers.append(int(label, 8))  # raises for `08`, as the parser fails it
        elif label.isdigit() and len(label) <= 10:
            numbers.append(int(label))
        else:
            raise ValueError(name)
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        raise ValueError(name)
    value = last + sum(n << (8 * (3 - i)) for i, n in enumerate(head))
    return str(ipaddress.IPv4Address(value))


def normalize_host(entry: str) -> str | None:
    """An `allowed_hosts` entry as someone typed it, in `host_name`'s form.

    A `Host` header carries the port, so `kraft.local:8765` is a natural thing
    to type, and so are `Kraft.Local` and `http://kraft.local/`. Kept as typed,
    none of them ever matched, and the browser got a 403 on its own board. The
    scheme, path and port go, and an IPv4 address written as one number or
    fewer than four (`127.1`) becomes the dotted quad a browser sends. None
    for what is still not one host name or IP: a wildcard, userinfo, a
    space, a port that is not a number, a number that is no IPv4 address."""
    text = entry.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = re.split(r"[/?#]", text, maxsplit=1)[0]
    try:
        # A bare IPv6 literal: its colons are not a port.
        text = f"[{ipaddress.IPv6Address(text)}]"
    except ValueError:
        pass
    try:
        _ = urlsplit(f"//{text}").port  # raises for a port that is not a number
    except ValueError:
        return None
    name = host_name(text)
    if name and not name.isascii():
        # UTS #46, as a browser maps a name before it sends it: Python's own
        # "idna" codec is IDNA 2003, which turns `faß.de` into `fass.de`
        # where the browser's Host says `xn--fa-hia.de`.
        try:
            name = idna.encode(name, uts46=True).decode("ascii")
        except idna.IDNAError:
            return None
    if name:
        try:
            name = _numeric_ipv4(name) or name
        except ValueError:
            return None
    return name if name and _HOST_NAME.fullmatch(name) else None


def host_entry_problem(entry: str) -> str | None:
    """Why `entry` can never be on `allowed_hosts`, naming it; None when
    `normalize_host` makes a host of it. One message for the save that refuses
    it and for the checks that find one already in `access.yaml`."""
    if normalize_host(entry) is not None:
        return None
    return (
        f"allowed_hosts: {entry!r} is not a host name or IP address, so no "
        "browser's Host ever matches it. Enter one name, such as kraft.local, "
        "192.168.1.5 or [fd00::5], with no wildcard or user@"
    )


class Access(_Model):
    FILE = "access.yaml"

    bind: str = "127.0.0.1"
    port: int = 8765
    password_hash: str | None = None
    session_expiry_days: int = 7
    allowed_hosts: list[str] = []

    @field_validator("allowed_hosts")
    @classmethod
    def _normalized_hosts(cls, value: list[str]) -> list[str]:
        """Each entry in the form the perimeter compares a `Host` in, once. An
        entry that is not a host at all is kept as written, not refused:
        `PUT /access` refuses one, but a file saved before that check must
        still load, and such an entry never matched anything anyway."""
        return list(dict.fromkeys(normalize_host(h) or h for h in value))


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


# ── sandbox ──────────────────────────────────────────────────────────────────


#: `templates/sandbox.yaml`: how this machine runs sandboxed tasks, as opposed
#: to `sandbox:` in repos.yaml or a policy, which says whether a task is
#: sandboxed and in what. Not bundled and not seeded: a missing file is all
#: defaults, which suit a machine with docker and no SELinux.
class SandboxHost(_Model):
    FILE = "sandbox.yaml"

    #: The container CLI. Unset: docker if it is on PATH, else podman.
    cli: Literal["docker", "podman"] | None = None
    #: What to do on a host where SELinux enforces, which denies every bind
    #: mount a container did not relabel. `auto` stops the task and says so:
    #: both answers change something outside Kraft, so neither is picked for
    #: the operator.
    selinux: Literal["auto", "relabel", "disable"] = "auto"
    #: Extra root certificates (PEM) a sandboxed task trusts on top of its
    #: image's own: a corporate TLS-intercepting proxy's CA, say. Unset: the
    #: daemon's `SSL_CERT_FILE`, if it has one. Read at each launch; a path
    #: that cannot be read stops the launch (`worker.backends.docker_forward`).
    ca_bundle: Path | None = None
    #: The image of the relay a session under `network:` gets: `--network
    #: none`, forwarding its 127.0.0.1:3128 to the session's proxy socket.
    #: Default: upstream socat 1.8.1.3, pinned by its multi-arch index digest.
    relay_image: str = Field(
        default="docker.io/alpine/socat@sha256:"
        "5ffbd6ae916cbad86a58fabe0d6d5a6fd5c2b47ddf031e82996baac9300e732f",
        min_length=1,
    )
    #: A `kind: kit` credential's value, by its credential@1 `service`: the
    #: name of the daemon's own environment variable holding it. A Kit never
    #: picks a host variable itself (credential@1: host environment variables
    #: never auto-inject).
    credentials: dict[
        Annotated[str, Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")],
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")],
    ] = {}

    @field_validator("ca_bundle")
    @classmethod
    def _absolute_ca_bundle(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError(f"must be an absolute path, not {str(value)!r}")
        return value


# ── notify ───────────────────────────────────────────────────────────────────


#: `templates/notify.yaml`. Not bundled and not seeded, for `access.yaml`'s
#: reason: it holds a secret and a hostname that belong to one machine. A
#: missing file reads as this, and the first `PUT /notify` creates it.
class Notify(_Model):
    FILE = "notify.yaml"

    enabled: bool = False
    url: str | None = None
    base_url: str | None = None
    events: list[str] = Field(
        ["gate_requested", "work_item_needs_human"],
        json_schema_extra={"items": {"type": "string", "enum": sorted(BY_VALUE)}},
    )

    @field_validator("events")
    @classmethod
    def _known_events(cls, events: list[str]) -> list[str]:
        unknown = [e for e in events if e not in BY_VALUE]
        if unknown:
            kind = "type" if len(unknown) == 1 else "types"
            # A mis-indented webhook URL can land here; echo only a short head of it.
            names = ", ".join(repr(e[:40] + "..." if len(e) > 40 else e) for e in unknown[:5])
            if len(unknown) > 5:
                names += f" and {len(unknown) - 5} more"
            raise ValueError(
                f"unknown event {kind} {names}; see the Events reference for the names"
            )
        return events

    @classmethod
    def load(cls, path: str | Path) -> Notify:
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
        try:
            return cls.model_validate(overrides)
        except ValidationError as exc:
            raise ConfigError(cls.refusal(exc)) from None

    @staticmethod
    def refusal(exc: ValidationError) -> str:
        # `input` echoes the offending value, which for `url` is the secret --
        # so name only the field and pydantic's message, never the value.
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"])
        return f"notify.yaml: {where}: {err['msg'].removeprefix('Value error, ')}"


# ── auto-intake ──────────────────────────────────────────────────────────────


class Schedule(_Model):
    """One `schedules:` entry of `intake.yaml`: a cron that files a work item,
    paused, as `kraft item create` would (`triggers.tick`)."""

    cron: str
    repo: str
    chain: str
    title: str
    description: str = ""

    @field_validator("cron")
    @classmethod
    def _cron_runs(cls, cron: str) -> str:
        """The scheduler's own check (`policy._cron_fields`), so a cron it
        cannot run is refused where the file is read -- by a reload, a
        Settings draft and the start-time move -- not every minute in the
        tick, as 1.4 refused a `policy.yaml` trigger's."""
        try:
            _cron_fields("cron", cron)
        except PolicyError as exc:
            raise PydanticCustomError("cron", str(exc).removeprefix("cron: ")) from None
        return cron


def schedule_refusals(entries: list, where: str) -> dict[int, str]:
    """Why `intake.yaml`'s `schedules:` would refuse each of `entries` it
    refuses, by index, named `{where}.N.field: reason`. What keeps a pre-2.0
    `policy.yaml` trigger from moving."""
    out = {}
    for index, entry in enumerate(entries):
        try:
            Schedule.model_validate(entry)
        except ValidationError as exc:
            err = exc.errors()[0]
            field = ".".join(str(p) for p in err["loc"])
            out[index] = f"{where}.{index}{'.' + field if field else ''}: {err['msg']}"
    return out


def schedule_refusal(entries: list, where: str) -> str | None:
    """The first of `schedule_refusals`, or None when it takes them all."""
    return next(iter(schedule_refusals(entries, where).values()), None)


class Intake(_Model):
    FILE = "intake.yaml"

    enabled: bool = False
    interval_s: int = 300
    repos: list[str] = []
    priority_ceiling: int = 2
    #: Cron-fired intake. `policy.yaml`'s `triggers:` before 2.0, which is
    #: still read (`policy.PolicyInput.triggers`) and named by `kraft admin doctor`.
    schedules: list[Schedule] = []

    @model_validator(mode="before")
    @classmethod
    def _drop_moved_keys(cls, data: Any) -> Any:
        """`max_concurrent` moved to `policy.yaml` before 2.0, where every slot
        count is read. A file still naming it loads, the key is not honoured,
        and the warning names the move; `kraft admin doctor` does too."""
        if isinstance(data, dict) and "max_concurrent" in data:
            data = {k: v for k, v in data.items() if k != "max_concurrent"}
            logger.warning(
                "intake.yaml: max_concurrent is read from policy.yaml, not here; "
                "move it there and delete this key"
            )
        return data


#: What a save fills an `intake.yaml` with for every key it did not have; the
#: schedules are written only when there are some.
INTAKE_DEFAULT: dict = Intake().model_dump(exclude={"schedules"})


# ── theme ────────────────────────────────────────────────────────────────────

PALETTE_IDS = frozenset({"nocturne", "rose", "forest", "amber", "slate"})


class BoardPrefs(_Model):
    group_by: Literal["status", "repo", "chain"] = "status"

    @model_validator(mode="before")
    @classmethod
    def _chain_was_template(cls, data: Any) -> Any:
        """`template` named the chain grouping before 2.0; read as `chain`."""
        if isinstance(data, dict) and data.get("group_by") == "template":
            data = {**data, "group_by": "chain"}
        return data

    show_done: int = Field(default=5, ge=1)
    open_in: Literal["peek", "full"] = "peek"


#: The V2 look an old `palette` stands for until the file names its own
#: (surface, accent), always at `colour_amount: full`, so nobody's theme
#: changes on upgrade. The accent is the nearest to the palette's old one.
PALETTE_V2 = {
    "nocturne": ("ink", "violet"),
    "rose": ("ink", "violet"),
    "forest": ("moss", "green"),
    "amber": ("sand", "amber"),
    "slate": ("slate", "blue"),
}
THEME_V2_KEYS = ("surface", "accent", "colour_amount")


class CodeScheme(_Model):
    light: Literal["auto", "none", "solarized-light"] = "auto"
    dark: Literal["auto", "none", "solarized-dark", "monokai", "dracula"] = "auto"


class DiffPrefs(_Model):
    layout: Literal["unified", "split"] = "unified"
    colours: Literal["theme", "safe", "plain"] = "theme"
    show_whitespace: bool = True
    word_highlight: bool = True
    wrap_lines: bool = False
    one_file_at_a_time: bool = True


#: The editors `POST /documents/{id}/open` can launch, each by an executable of
#: the same name. The UI's default editor is one of these, or unset for the system's.
EDITORS = ("code", "cursor", "zed", "obsidian")


class Theme(_Model):
    FILE = "theme.yaml"

    # Legacy: the shipped UI's colour model. Read while present (an old tab or
    # a hand edit can still write it) and converted at the next start by
    # `migrate_theme`; never written back.
    palette: str | None = None
    mode: Literal["light", "dark", "system"] = "dark"
    density: Literal["compact", "comfortable"] = "compact"
    board: BoardPrefs = BoardPrefs()
    # The new UI's colour model (UX V2). Unset means derived from `palette`.
    surface: Literal["graphite", "slate", "ink", "sand", "moss"] | None = None
    accent: Literal["none", "blue", "violet", "green", "amber", "rose"] | None = None
    colour_amount: Literal["mono", "subtle", "full"] | None = None
    code_scheme: CodeScheme = CodeScheme()
    diff: DiffPrefs = DiffPrefs()
    # The document viewer's Open in editor; unset is the system's default app.
    editor: Literal[EDITORS] | None = None

    @field_validator("palette")
    @classmethod
    def _known_palette(cls, v: str | None) -> str | None:
        if v is not None and v not in PALETTE_IDS:
            raise ValueError(f"unknown palette: {v!r}")
        return v

    @model_validator(mode="after")
    def _mono_has_no_accent(self) -> Self:
        if self.colour_amount == "mono" and self.accent not in (None, "none"):
            raise ValueError(
                f"accent {self.accent!r} needs colour_amount subtle or full; mono has no accent"
            )
        return self

    def save(self, path: str | Path) -> None:
        # An unset V2 key stays out of the file, so it keeps deriving.
        write_yaml(path, self.model_dump(exclude_none=True))

    def effective(self) -> dict:
        """What `GET /theme` answers: every V2 key filled, from `palette` (or
        nocturne's look, with no file) when the file names no surface
        (`derived: true`). `palette` itself is not in the answer."""
        out = self.model_dump(exclude={"palette"})
        derived = self.surface is None
        if derived:
            surface, accent = PALETTE_V2[self.palette or "nocturne"]
            amount = self.colour_amount or "full"
        else:
            surface, accent, amount = self.surface, "none", self.colour_amount or "subtle"
        out["surface"] = surface
        out["colour_amount"] = amount
        out["accent"] = self.accent or ("none" if amount == "mono" else accent)
        out["derived"] = derived
        return out


#: What the 2.0 upgrade saves the original `theme.yaml` as, beside it.
THEME_BACKUP = ".pre-2.0"
#: What the 1.5.0 release candidates, which became 2.0, saved it as: rc5 to
#: rc9 `.pre-ux2`, later ones `.pre-1.5`. One of these that exists is the
#: backup: it holds the file from before the first conversion, so a later one
#: never writes a second copy or replaces it. Oldest first, which is the one
#: that holds the original when a home has both.
EARLIER_THEME_BACKUPS = (".pre-ux2", ".pre-1.5")


def theme_backup(path: str | Path) -> Path:
    """Where `migrate_theme` keeps (or kept) the original of `path`: the
    earlier pre-release's copy when one exists, else `<name>.pre-2.0`."""
    path = Path(path)
    for suffix in EARLIER_THEME_BACKUPS:
        earlier = path.with_name(path.name + suffix)
        if earlier.exists():
            return earlier
    return path.with_name(path.name + THEME_BACKUP)


def migrate_theme(path: str | Path) -> bool:
    """The 2.0 upgrade: write the look `palette` stands for as the file's own
    `surface`, `accent` and `colour_amount`, then drop `palette`. All three are
    written: a file with its own surface defaults to no accent at subtle, so
    `surface` alone would change the look. `effective()` answers the same
    before and after, which is the point: the user sees no change.

    The original bytes go to `theme.yaml.pre-2.0` first (`theme_backup`),
    never overwriting an earlier copy, and none is written beside a
    `theme.yaml.pre-ux2` or `theme.yaml.pre-1.5` a 1.5.0 release candidate
    left. A missing, unreadable or invalid file, or one without `palette`, is
    left alone. True when the file was rewritten."""
    path = Path(path)
    try:
        data = read_yaml(path)
        look = Theme.model_validate(data).effective()
    except (ConfigError, ValidationError):
        return False
    if "palette" not in data:
        return False
    if "surface" not in data:
        data.update({key: look[key] for key in THEME_V2_KEYS})
    del data["palette"]
    backup = theme_backup(path)
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    write_yaml(path, data)
    return True
