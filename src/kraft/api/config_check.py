"""Every Kraft config file checked as saving it would check it, writing
nothing (VS Code extension spec, "Config files"). The settings `PUT` routes
call the same functions, so a check and a save cannot disagree."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ValidationError

from kraft import config as config_mod
from kraft import harness as harness_mod
from kraft import policy as policy_mod
from kraft.adapters.agent import HarnessUnavailable, select_profile
from kraft.executor import fallback
from kraft.templates import positions
from kraft.templates.environment import (
    AgentProfileInput,
    HarnessProfileInput,
    HarnessProfileTable,
    TemplateEnvironmentError,
)
from kraft.templates.library import (
    CHAINS_DIR,
    LIBRARY_FILE,
    TemplateIssue,
    TemplateLibrary,
    TemplateLibraryError,
)
from kraft.templates.models import AgentTask, first_error, retired_keys
from kraft.worker import steering as steering_mod

FILES = frozenset(
    {
        "library.yaml",
        "policy.yaml",
        "harnesses.yaml",
        "repos.yaml",
        "intake.yaml",
        "access.yaml",
        "theme.yaml",
        "notify.yaml",
    }
)
CHAIN_FILE = re.compile(r"chains/([a-z][a-z0-9_-]*)\.yaml")
NOT_A_MAPPING = "a Kraft config file is a mapping"


@dataclass(frozen=True)
class CheckContext:
    templates_dir: Path
    library: TemplateLibrary | None
    skills_dir: Path | None
    instance_policy: policy_mod.InstancePolicy | None
    providers: Mapping[str, harness_mod.Harness]


def context(st) -> CheckContext:
    return CheckContext(
        templates_dir=st.templates_dir,
        library=getattr(st, "library", None),
        skills_dir=getattr(st, "skills_dir", None),
        instance_policy=getattr(st, "instance_policy", None),
        providers=harness_mod.load(None).valid,
    )


def check(relative: str, text: str, ctx: CheckContext) -> list[TemplateIssue]:
    """Every problem saving `text` as `relative` would refuse it for. Raises
    `ValueError` for a path that is not a Kraft config file."""
    chain = CHAIN_FILE.fullmatch(relative)
    if chain is None and relative not in FILES:
        raise ValueError(f"not a Kraft config file: {relative!r}")
    path = ctx.templates_dir / relative
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [TemplateIssue(path, None, f"not YAML: {exc}", mark=positions.yaml_mark(exc))]
    data = {} if data is None else data
    if not isinstance(data, dict):
        return [TemplateIssue(path, None, NOT_A_MAPPING)]
    checker = _check_chain if chain is not None else _CHECKERS[relative]
    return checker(path, data, ctx)


def _pydantic_issue(path: Path, exc: ValidationError) -> TemplateIssue:
    return TemplateIssue(path, None, first_error(exc), loc=tuple(exc.errors()[0]["loc"]))


def _first_loc(
    model: type[BaseModel], data: object, prefix: tuple[object, ...] = ()
) -> tuple[object, ...] | None:
    """Where `model` first refuses `data`, or `None` when it accepts it: a
    position for a loader error whose message carries none."""
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return (*prefix, *exc.errors()[0]["loc"])
    return None


def retired_message(data: dict) -> str | None:
    if retired := retired_keys(data):
        return (
            f"{retired[0]} is retired (Ruling 196): a wait's timeout is its task's own "
            "policy.total_time_cap_minutes"
        )
    return None


# ── chains and the library ──


def chain_issues(
    library: TemplateLibrary | None,
    path: Path,
    tid: str,
    data: dict,
    instance_policy: policy_mod.InstancePolicy | None,
) -> list[TemplateIssue]:
    if why := retired_message(data):
        return [TemplateIssue(path, tid, why)]
    if data.get("id", tid) != tid:
        return [
            TemplateIssue(
                path, tid, f"the file declares id {data['id']!r}, not {tid!r}", loc=("id",)
            )
        ]
    if library is None:
        return [
            TemplateIssue(
                path, tid, f"the template library does not load; fix {LIBRARY_FILE} first"
            )
        ]
    try:
        candidate, _ = library.with_chain(path, {**data, "id": tid})
    except TemplateLibraryError as exc:
        return [TemplateIssue.from_error(path, tid, exc)]
    return [i for i in candidate.lint(instance_policy) if i.chain == tid]


def _check_chain(path, data, ctx):
    return chain_issues(ctx.library, path, path.stem, data, ctx.instance_policy)


def library_issues(
    library: TemplateLibrary | None,
    data: dict,
    path: Path,
    instance_policy: policy_mod.InstancePolicy | None,
    repos: list[config_mod.RepoEntry],
    skills_dir: Path | None = None,
) -> list[TemplateIssue]:
    if why := retired_message(data):
        return [TemplateIssue(path, None, why)]
    try:
        if library is None:
            # No running library to diff against or take chains from: every
            # chain the candidate cannot resolve is new, read from disk.
            broken_before: set[str | None] = set()
            candidate = TemplateLibrary.from_mappings(
                data, _chains_on_disk(path.parent), library_path=path, skills_dir=skills_dir
            )
        else:
            broken_before = {i.chain for i in library.lint(instance_policy)}
            candidate = library.with_library(data, path)
    except TemplateLibraryError as exc:
        return [TemplateIssue.from_error(path, None, exc)]
    issues = [i for i in candidate.lint(instance_policy) if i.chain not in broken_before]
    # A repository's `steering:` names library profiles too: removing (or
    # over-growing) one it names is refused like a chain it would break.
    profiles = {n: p.instructions for n, p in candidate.steering.items()}
    for entry in repos:
        try:
            steering_mod.select(entry.steering, profiles, where=f"repos.yaml: {entry.path}")
        except steering_mod.SteeringError as exc:
            issues.append(TemplateIssue(path, None, str(exc)))
    return issues


def _chains_on_disk(root: Path) -> list[tuple[Path, dict]]:
    """Every chain file under `root` that parses to a mapping. One that does
    not was broken before this edit, so it is not the edit's to report."""
    chains = []
    for chain_path in sorted((root / CHAINS_DIR).glob("*.yaml")):
        try:
            body = yaml.safe_load(chain_path.read_text())
        except OSError, yaml.YAMLError:
            continue
        if isinstance(body, dict):
            chains.append((chain_path, body))
    return chains


def _check_library(path, data, ctx):
    # A repos.yaml broken for another reason is not this save's to refuse.
    try:
        repos = config_mod.load_repos(ctx.templates_dir / "repos.yaml")
    except config_mod.ConfigError:
        repos = []
    return library_issues(ctx.library, data, path, ctx.instance_policy, repos, ctx.skills_dir)


# ── policy ──


def validate_policy(data: dict) -> tuple[policy_mod.PolicyInput, policy_mod.Policy]:
    """Raises `PolicyError`."""
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "policy.yaml"
        candidate.write_text(yaml.safe_dump(data))
        parsed = policy_mod.PolicyInput.from_yaml(candidate)
        return parsed, policy_mod.Policy.from_input(parsed, source=candidate)


def _check_policy(path, data, ctx):
    try:
        validate_policy(data)
    except policy_mod.PolicyError as exc:
        return [TemplateIssue(path, None, str(exc), loc=_first_loc(policy_mod.PolicyInput, data))]
    return []


# ── intake ──


class IntakeBody(BaseModel):
    """`intake.yaml`, typed. Unlike `policy.yaml` this file is a flat fixed
    shape, so the bounds live here rather than in a loader that has to accept a
    hand-edited file it did not write."""

    enabled: bool
    # The poller floors this at 30s anyway; rejecting it is better than
    # accepting a number the running instance will not honour.
    interval_s: int = Field(ge=30)
    # Moved to `policy.yaml` (`Policy.max_concurrent`); kept optional here for
    # one release so an old client or a hand-edited file round-trips without
    # a 422. No longer read back as authoritative anywhere.
    max_concurrent: int | None = Field(default=None, ge=1)
    # P0 is the *highest* priority, so the ceiling is "P<n> and below".
    priority_ceiling: int = Field(ge=0, le=4)
    repos: list[str] = []


def _check_intake(path, data, ctx):
    for model in (IntakeBody, config_mod.Intake):
        try:
            model.model_validate(data)
        except ValidationError as exc:
            return [_pydantic_issue(path, exc)]
    return []


# ── access, theme, notify ──


def access_problem(access: Mapping) -> str | None:
    # Binding off-localhost without a password is the configuration that puts an
    # agent runner on the office wifi. Refuse it rather than allow it quietly.
    bind = access.get("bind", config_mod.Access().bind)
    if bind not in config_mod.LOOPBACK and not access.get("password_hash"):
        return "set a password before binding off localhost"
    return None


def _check_access(path, data, ctx):
    try:
        config_mod.Access.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
    if why := access_problem(data):
        return [TemplateIssue(path, None, why, loc=("bind",))]
    return []


def _check_theme(path, data, ctx):
    try:
        config_mod.Theme.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
    return []


def url_problem(value: str, field: str) -> str | None:
    if urlsplit(value).scheme not in ("http", "https"):
        return f"{field} must be an http or https URL"
    return None


def notify_problem(cfg: Mapping) -> str | None:
    for field in ("url", "base_url"):
        if cfg.get(field) and (why := url_problem(cfg[field], field)):
            return why
    # Enabled with nowhere to send is a setting that looks armed and is not.
    if cfg.get("enabled") and not cfg.get("url"):
        return "set a webhook URL before enabling notifications"
    return None


def _check_notify(path, data, ctx):
    try:
        config_mod.Notify.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
    if why := notify_problem(data):
        field = (
            "enabled"
            if why.startswith("set a webhook")
            else next(f for f in ("url", "base_url") if why.startswith(f))
        )
        return [TemplateIssue(path, None, why, loc=(field,))]
    return []


# ── harnesses ──


def selections(library: TemplateLibrary | None) -> list[tuple[str, str, AgentTask]]:
    """(chain, task path, task) for every agent task of every chain that resolves."""
    out = []
    for chain in library.chain_ids if library is not None else ():
        try:
            resolved = library.resolve_chain(chain)
        except TemplateLibraryError:
            continue  # not resolving already; `lint` says why
        for node in resolved.nodes:
            out += [(chain, t.path, t.task) for t in node.tasks() if isinstance(t.task, AgentTask)]
    return out


def launch_problems(
    selections, table: HarnessProfileTable | None, path, providers
) -> dict[tuple[str, str], str]:
    """Why each selecting task, keyed (chain, task path), could not launch on
    `table` (`None`: the file does not load, so none can): the launch's own
    checks, including its agent profile's pairing (Kraft-ps1ao), plus a task's
    own `model`/`effort` against its profile's provider."""
    problems = {}
    for chain, task_path, task in selections:
        where = f"chain {chain!r} task {task_path!r}: harness {task.harness!r}"
        if table is None:
            problems[chain, task_path] = f"{where}: {path} does not load"
            continue
        try:
            profile = select_profile(table.profiles, task.harness, path)
        except HarnessUnavailable as exc:
            problems[chain, task_path] = f"{where}: {exc}"
            continue
        if why := route_problem(task, profile, table, providers):
            problems[chain, task_path] = f"chain {chain!r} task {task_path!r}: {why}"
        # Each fallback entry must pair with its harness too, from the task's
        # own list or its profile's. A disabled one is not a pairing problem:
        # the launch skips it (`executor.fallback`).
        entries, source = fallback.fallback_list(task, table)
        for n, entry in enumerate(entries):
            cand = fallback.apply(task, entry)
            at = f"chain {chain!r} task {task_path!r}: fallback entry {n} ({source})"
            harness = table.profiles.get(cand.harness)
            if harness is None:
                why = f"harness {cand.harness!r} is not in {path}"
            else:
                why = route_problem(cand, harness, table, providers)
            if why:
                problems[chain, f"{task_path} fallback[{n}]"] = f"{at}: {why}"
    return problems


def route_problem(task: AgentTask, harness, table: HarnessProfileTable, providers) -> str | None:
    """Why `task`'s route (its agent profile, or its own model/effort) cannot
    run on `harness`, or None."""
    if task.profile is not None:
        return table.pairing_problem(task.profile, harness, providers)
    for option in ("model", "effort"):
        value = getattr(task, option)
        if value is not None and not providers[harness.provider].value_ok(option, value):
            return (
                f"harness {harness.id!r}: provider {harness.provider!r} takes no {option} {value!r}"
            )
    return None


def harness_breakage(
    library: TemplateLibrary | None,
    current: dict | None,
    candidate: dict,
    path: Path,
    providers: Mapping[str, harness_mod.Harness],
) -> str | None:
    """Why `candidate` (the whole harnesses.yaml) must not be saved over
    `current`, or None. A task that could not launch before is not the
    edit's to fix."""
    try:
        before_table = (
            HarnessProfileTable.from_mapping(current, path, harnesses=providers)
            if current is not None
            else None
        )
    except TemplateEnvironmentError:
        before_table = None
    try:
        table = HarnessProfileTable.from_mapping(candidate, path, harnesses=providers)
    except TemplateEnvironmentError as exc:
        return str(exc)
    chosen = selections(library)
    before = launch_problems(chosen, before_table, path, providers)
    after = launch_problems(chosen, table, path, providers)
    broken = [m for key, m in sorted(after.items()) if key not in before]
    return broken[0] if broken else None


def _check_harnesses(path, data, ctx):
    try:
        current = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
    except OSError, yaml.YAMLError:
        current = None
    if not isinstance(current, dict):
        current = None
    if why := harness_breakage(ctx.library, current, data, path, ctx.providers):
        loc = None
        for section, model in (("harnesses", HarnessProfileInput), ("profiles", AgentProfileInput)):
            entries = data.get(section)
            for name, body in entries.items() if isinstance(entries, dict) else ():
                loc = loc or _first_loc(model, body, (section, name))
        return [TemplateIssue(path, None, why, loc=loc)]
    return []


# ── repos ──


def _check_repos(path, data, ctx):
    profiles = {n: p.instructions for n, p in ctx.library.steering.items()} if ctx.library else None
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "repos.yaml"
        candidate.write_text(yaml.safe_dump(data))
        try:
            config_mod.load_repos(candidate, steering=profiles)
        except config_mod.ConfigError as exc:
            entries = data.get("repos")
            loc = None
            for index, entry in enumerate(entries if isinstance(entries, list) else ()):
                loc = loc or _first_loc(config_mod.RepoEntry, entry, ("repos", index))
            message = str(exc).replace(str(candidate), str(path))
            return [TemplateIssue(path, None, message, loc=loc)]
    return []


_CHECKERS: dict[str, Callable[[Path, dict, CheckContext], list[TemplateIssue]]] = {
    "library.yaml": _check_library,
    "policy.yaml": _check_policy,
    "harnesses.yaml": _check_harnesses,
    "repos.yaml": _check_repos,
    "intake.yaml": _check_intake,
    "access.yaml": _check_access,
    "theme.yaml": _check_theme,
    "notify.yaml": _check_notify,
}
