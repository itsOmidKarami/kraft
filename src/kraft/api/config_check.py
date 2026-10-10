"""Every Kraft config file checked as saving it would check it, writing
nothing (VS Code extension spec, "Config files"). The settings `PUT` routes
call the same functions, so a check and a save cannot disagree."""

from __future__ import annotations

import functools
import re
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ValidationError

from kraft import config as config_mod
from kraft import harness as harness_mod
from kraft import policy as policy_mod
from kraft.adapters.agent import HarnessUnavailable, select_profile
from kraft.executor import fallback
from kraft.plugins import load as plugins_load
from kraft.plugins import manifest as plugin_manifest
from kraft.plugins.config import PluginsConfig, PluginsLock
from kraft.templates import positions
from kraft.templates.environment import (
    AgentProfileInput,
    HarnessProfileInput,
    HarnessProfileTable,
    PairingProblem,
    TemplateEnvironmentError,
)
from kraft.templates.library import (
    CHAINS_DIR,
    LIBRARY_FILE,
    ComponentSource,
    Namespace,
    TemplateIssue,
    TemplateLibrary,
    TemplateLibraryError,
    _Resolution,
)
from kraft.templates.models import PATH_SEPARATOR, AgentTask, first_error, retired_keys
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
        "plugins.yaml",
        "plugins.lock",
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
    #: The installed plugins the running library was built with.
    plugins: tuple[plugins_load.InstalledPlugin, ...] = ()
    #: `run/plugins`, where their stores are. None: this process's own.
    plugins_dir: Path | None = None


def context(st) -> CheckContext:
    return CheckContext(
        templates_dir=st.templates_dir,
        library=getattr(st, "library", None),
        skills_dir=getattr(st, "skills_dir", None),
        instance_policy=getattr(st, "instance_policy", None),
        providers=harness_mod.load(None).valid,
        plugins=getattr(getattr(st, "library", None), "plugins", ()),
        plugins_dir=st.run_dirs.plugins if getattr(st, "run_dirs", None) else None,
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
            f"{retired[0]} is retired: a wait's timeout is its task's own "
            "policy.total_time_cap_minutes"
        )
    return None


# ── chains and the library ──


@functools.cache
def lucide_icons() -> frozenset[str]:
    """The icon names the UI can draw: `lucide_icons.txt`, which `just icons`
    writes from the installed `lucide-react`."""
    text = resources.files("kraft.templates").joinpath("lucide_icons.txt").read_text()
    return frozenset(text.split())


def _icons(data: object, loc: tuple[object, ...] = ()) -> Iterator[tuple[tuple[object, ...], str]]:
    """Every `icon:` string in an authored mapping, with its key path. Only
    the components the model gives an `icon` can hold one; anywhere else the
    schema refuses it."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "icon" and isinstance(value, str):
                yield (*loc, key), value
            else:
                yield from _icons(value, (*loc, key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from _icons(value, (*loc, index))


def icon_issues(
    file: Path, chain: str | None, data: dict, where: Callable[[tuple], str | None] = lambda _: None
) -> list[TemplateIssue]:
    """Each icon in `data` the UI has no drawing for. A lint problem, never a
    load failure (R32): the chain still runs, and the UI draws the kind's own
    icon instead. `where` names the component a key path belongs to."""
    return [
        TemplateIssue(
            file,
            chain,
            f"{where(loc) or PATH_SEPARATOR.join(map(str, loc[:-1]))}: unknown icon {name!r}",
            loc=loc,
        )
        for loc, name in _icons(data)
        if name not in lucide_icons()
    ]


def chain_icon_issues(
    library: TemplateLibrary, path: Path, tid: str, data: dict
) -> list[TemplateIssue]:
    """`icon_issues` for one chain file, each named by its canonical path.
    Only the icons the file itself writes: an inherited one is the library's."""
    resolution = _Resolution(library, ComponentSource(path, Namespace.NODES, tid))
    try:
        resolution.expand_chain({**data, "id": tid})
    except TemplateLibraryError:
        pass  # what it reached still names its paths; why it stopped is lint's
    return icon_issues(path, tid, data, lambda loc: resolution.split(loc)[0])


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
    issues = [i for i in candidate.lint(instance_policy) if i.chain == tid]
    return issues + chain_icon_issues(candidate, path, tid, data)


def _check_chain(path, data, ctx):
    return chain_issues(ctx.library, path, path.stem, data, ctx.instance_policy)


def library_candidate(
    library: TemplateLibrary | None,
    data: dict,
    path: Path,
    skills_dir: Path | None = None,
    chains: Iterable[tuple[Path, Mapping]] = (),
) -> TemplateLibrary:
    """`data` as `library.yaml` with the running library's chains (with none
    running, the ones on disk), each of `chains` in place of the one of its id.
    Raises `TemplateLibraryError`."""
    if library is None:
        candidate = TemplateLibrary.from_mappings(
            data,
            _chains_on_disk(path.parent),
            library_path=path,
            skills_dir=skills_dir,
            plugins=plugins_load.installed(path.parent),
        )
    else:
        candidate = library.with_library(data, path)
    for chain_path, body in chains:
        candidate, _ = candidate.with_chain(chain_path, body)
    return candidate


def library_issues(
    library: TemplateLibrary | None,
    data: dict,
    path: Path,
    instance_policy: policy_mod.InstancePolicy | None,
    repos: list[config_mod.RepoEntry],
    skills_dir: Path | None = None,
    chains: Iterable[tuple[Path, Mapping]] = (),
) -> list[TemplateIssue]:
    """`chains`: chain files saved with the library, as `library_candidate`
    takes them (a library draft's renames)."""
    if why := retired_message(data):
        return [TemplateIssue(path, None, why)]
    # No running library to diff against: every chain the candidate cannot
    # resolve is new.
    broken_before = {i.chain for i in library.lint(instance_policy)} if library else set()
    try:
        candidate = library_candidate(library, data, path, skills_dir, chains)
    except TemplateLibraryError as exc:
        return [TemplateIssue.from_error(path, None, exc)]
    issues = [i for i in candidate.lint(instance_policy) if i.chain not in broken_before]
    issues += icon_issues(path, None, data)
    issues += [TemplateIssue(path, None, why) for _, why in steering_issues(candidate, repos)]
    return issues


def steering_issues(
    library: TemplateLibrary, repos: list[config_mod.RepoEntry]
) -> list[tuple[config_mod.RepoEntry, str]]:
    """A repository's `steering:` names library profiles too: removing (or
    over-growing) one it names is refused like a chain it would break."""
    profiles = {n: p.instructions for n, p in library.steering.items()}
    out = []
    for entry in repos:
        try:
            steering_mod.select(entry.steering, profiles, where=f"repos.yaml: {entry.path}")
        except steering_mod.SteeringError as exc:
            out.append((entry, str(exc)))
    return out


def _chains_on_disk(root: Path) -> list[tuple[Path, dict]]:
    """Every chain file under `root` that parses to a mapping. One that does
    not was broken before this edit, so it is not the edit's to report."""
    chains = []
    for chain_path in sorted((root / CHAINS_DIR).glob("*.yaml")):
        try:
            body = yaml.safe_load(chain_path.read_text())
        except (OSError, yaml.YAMLError):
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
        parsed, _ = validate_policy(data)
    except policy_mod.PolicyError as exc:
        return [TemplateIssue(path, None, str(exc), loc=_first_loc(policy_mod.PolicyInput, data))]
    instance = parsed.instance_policy()
    return [
        TemplateIssue(path, None, f"would leave plugin {plugin.id} out: {why}", loc=("maxima",))
        for plugin in ctx.plugins
        if plugin.left_out is None
        and (why := plugins_load.limits_problem(plugin, instance)) is not None
    ]


# ── intake ──


class IntakeBody(BaseModel):
    """`intake.yaml`, typed. Unlike `policy.yaml` this file is a flat fixed
    shape, so the bounds live here rather than in a loader that has to accept a
    hand-edited file it did not write."""

    enabled: bool
    # The poller floors this at 30s anyway; rejecting it is better than
    # accepting a number the running instance will not honour.
    interval_s: int = Field(ge=30)
    # P0 is the *highest* priority, so the ceiling is "P<n> and below".
    priority_ceiling: int = Field(ge=0, le=4)
    repos: list[str] = []
    #: Left out, the file's own are kept: a 1.x client never sent any.
    schedules: list[dict] | None = None


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
    # `Access` loads an entry that is not a host as written, so nothing else
    # would say it never matches.
    return [
        TemplateIssue(path, None, why, loc=("allowed_hosts", index))
        for index, entry in enumerate(data.get("allowed_hosts") or [])
        if (why := config_mod.host_entry_problem(entry))
    ]


def _check_theme(path, data, ctx):
    try:
        config_mod.Theme.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
    return []


def url_problem(value: str, field: str) -> str | None:
    try:
        scheme = urlsplit(value).scheme
    except ValueError:  # `http://[x`: urlsplit refuses an unclosed IPv6 bracket
        scheme = ""
    if scheme not in ("http", "https"):
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
        return [
            TemplateIssue(
                path, None, config_mod.Notify.refusal(exc), loc=tuple(exc.errors()[0]["loc"])
            )
        ]
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


@dataclass(frozen=True)
class LaunchProblem:
    """Why one selecting task (or one of its fallback entries) could not
    launch. `profile`, `provider` and `field` say which pairing failed when
    one did; they are None for a harness that is missing or a file that does
    not load."""

    chain: str
    #: The task's path, `<path> fallback[n]` for a fallback entry.
    task: str
    message: str
    profile: str | None = None
    provider: str | None = None
    field: str | None = None

    def view(self) -> dict:
        return {
            "chain": self.chain,
            "task": self.task,
            "profile": self.profile,
            "provider": self.provider,
            "field": self.field,
            "message": self.message,
        }


def launch_problem_details(
    selections, table: HarnessProfileTable | None, path, providers
) -> dict[tuple[str, str], LaunchProblem]:
    """Why each selecting task, keyed (chain, task path), could not launch on
    `table` (`None`: the file does not load, so none can): the launch's own
    checks, including its agent profile's pairing (Kraft-ps1ao), plus a task's
    own `model`/`effort` against its profile's provider."""
    problems = {}
    for chain, task_path, task in selections:
        where = f"chain {chain!r} task {task_path!r}: harness {task.harness!r}"
        if table is None:
            problems[chain, task_path] = LaunchProblem(
                chain, task_path, f"{where}: {path} does not load"
            )
            continue
        try:
            profile = select_profile(table.profiles, task.harness, path)
        except HarnessUnavailable as exc:
            problems[chain, task_path] = LaunchProblem(chain, task_path, f"{where}: {exc}")
            continue
        if why := route_problem_detail(task, profile, table, providers):
            problems[chain, task_path] = LaunchProblem(
                chain,
                task_path,
                f"chain {chain!r} task {task_path!r}: {why.message}",
                why.profile,
                why.provider,
                why.field,
            )
        # Each fallback entry must pair with its harness too, from the task's
        # own list or its profile's. A disabled one is not a pairing problem:
        # the launch skips it (`executor.fallback`).
        entries, source = fallback.fallback_list(task, table)
        for n, entry in enumerate(entries):
            cand = fallback.apply(task, entry)
            at = f"chain {chain!r} task {task_path!r}: fallback entry {n} ({source})"
            harness = table.profiles.get(cand.harness)
            key = f"{task_path} fallback[{n}]"
            if harness is None:
                problems[chain, key] = LaunchProblem(
                    chain, key, f"{at}: harness {cand.harness!r} is not in {path}"
                )
            elif why := route_problem_detail(cand, harness, table, providers):
                problems[chain, key] = LaunchProblem(
                    chain, key, f"{at}: {why.message}", why.profile, why.provider, why.field
                )
    return problems


def launch_problems(
    selections, table: HarnessProfileTable | None, path, providers
) -> dict[tuple[str, str], str]:
    """`launch_problem_details`' messages, keyed the same way."""
    return {
        key: p.message
        for key, p in launch_problem_details(selections, table, path, providers).items()
    }


def lint_report(
    templates_dir: Path,
    *,
    skills_dir: Path | None = None,
    instance_policy: policy_mod.InstancePolicy | None = None,
    plugins: Sequence[plugins_load.InstalledPlugin] | None = (),
) -> dict:
    """`kraft admin templates lint`'s answer, from the route and `--dir` alike:
    `TemplateLibrary.lint_dir`, plus each agent task of a resolving chain that
    `harnesses.yaml` could not launch -- a `profile:` with no model for its
    harness's provider, say (Kraft-9efnk.34), and each unknown icon
    (`icon_issues`). A `harnesses.yaml` that does not load adds nothing:
    doctor's `harnesses.yaml` row already says why."""
    templates_dir = Path(templates_dir)
    report = TemplateLibrary.lint_dir(
        templates_dir, skills_dir=skills_dir, instance_policy=instance_policy, plugins=plugins
    )
    issues = list(report.issues)
    path = templates_dir / "harnesses.yaml"
    providers = harness_mod.load(None).valid
    try:
        library = TemplateLibrary.from_yaml_dir(
            templates_dir, skills_dir=skills_dir, plugins=plugins or ()
        )
    except TemplateLibraryError:
        library = None
    if library is not None:
        library_path = templates_dir / LIBRARY_FILE
        issues += icon_issues(library_path, None, yaml.safe_load(library_path.read_text()) or {})
        for chain in library.chain_ids:
            file = library.chain_file(chain)
            issues += chain_icon_issues(library, file, chain, dict(library.chain_data(chain)))
        try:
            table = HarnessProfileTable.from_yaml(path, harnesses=providers, plugins=plugins or ())
        except TemplateEnvironmentError:
            table = None
        problems = (
            launch_problems(selections(library), table, path, providers)
            if table is not None
            else {}
        )
        for (chain, _), why in sorted(problems.items()):
            issues.append(TemplateIssue(library.chain_file(chain), chain, why))
    failed = {issue.chain for issue in issues}
    return {
        "valid": not issues,
        "chains": [id for id in report.chains if id not in failed],
        "issues": [positions.issue_view(i) for i in issues],
        # Offline (`--dir`) only: chains that reference a plugin, left unjudged.
        "unchecked": [positions.issue_view(i) for i in report.unchecked],
    }


def route_problem_detail(
    task: AgentTask, harness, table: HarnessProfileTable, providers
) -> PairingProblem | None:
    """Why `task`'s route (its agent profile, or its own model/effort) cannot
    run on `harness`, or None."""
    if task.profile is not None:
        return table.pairing_detail(task.profile, harness, providers)
    for option in ("model", "effort"):
        value = getattr(task, option)
        if value is not None and not providers[harness.provider].value_ok(option, value):
            return PairingProblem(
                None,
                harness.provider,
                option,
                f"harness {harness.id!r}: provider {harness.provider!r} takes no "
                f"{option} {value!r}",
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
    plugins = library.plugins if library is not None else ()
    try:
        before_table = (
            HarnessProfileTable.from_mapping(current, path, harnesses=providers, plugins=plugins)
            if current is not None
            else None
        )
    except TemplateEnvironmentError:
        before_table = None
    try:
        table = HarnessProfileTable.from_mapping(
            candidate, path, harnesses=providers, plugins=plugins
        )
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
    except (OSError, yaml.YAMLError):
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
    return [
        TemplateIssue(path, None, f"would leave plugin {plugin.id} out: {why}")
        for plugin in ctx.plugins
        if plugin.left_out is None
        and (why := _unmet_requires(plugin, ctx, harnesses=data)) is not None
    ]


def _unmet_requires(
    plugin, ctx: CheckContext, *, harnesses: dict | None = None, repos: dict | None = None
) -> str | None:
    """Why `plugin` would not load once `harnesses` is saved as harnesses.yaml,
    or `repos` as repos.yaml."""
    try:
        declared = plugin_manifest.plugin(
            plugin_manifest.parse(
                (plugin.root / plugin_manifest.PLUGIN_JSON).read_text(), plugin_manifest.PLUGIN_JSON
            ),
            plugin_manifest.PLUGIN_JSON,
        )
    except (OSError, ValueError, plugin_manifest.ManifestError):
        return None  # already not loading; the load says why
    return plugins_load.instance_problem(
        plugin.namespace, declared.requires, ctx.templates_dir, harnesses, repos
    )


# ── plugins ──


def _check_plugins(path, data, ctx):
    """A hand edit of `plugins.yaml`: the file itself, then what it would stop
    resolving. Disabling or dropping an entry unloads its plugin, and a new
    entry reserves its namespace before anything is installed under it."""
    from kraft.plugins import update as plugin_update

    try:
        config = PluginsConfig.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
    root = ctx.plugins_dir or plugins_load.plugins_dir()
    now = plugins_load.installed(ctx.templates_dir, root)
    then = plugins_load.installed(ctx.templates_dir, root, config=config)

    def loads(plugins):
        return {(p.id, p.namespace, p.left_out is None) for p in plugins}

    if loads(now) == loads(then):
        return []
    try:
        broken = plugin_update.breaks(
            plugin_update.state_of(ctx.templates_dir, now),
            plugin_update.state_of(ctx.templates_dir, then),
            ctx.templates_dir,
        )
    except policy_mod.PolicyError:
        return []  # policy.yaml's own check says why
    return [TemplateIssue(path, None, why, loc=("plugins",)) for why in broken]


def _check_lock(path, data, ctx):
    try:
        PluginsLock.model_validate(data)
    except ValidationError as exc:
        return [_pydantic_issue(path, exc)]
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
    return [
        TemplateIssue(path, None, f"would leave plugin {plugin.id} out: {why}")
        for plugin in ctx.plugins
        if plugin.left_out is None and (why := _unmet_requires(plugin, ctx, repos=data)) is not None
    ]


_CHECKERS: dict[str, Callable[[Path, dict, CheckContext], list[TemplateIssue]]] = {
    "library.yaml": _check_library,
    "policy.yaml": _check_policy,
    "harnesses.yaml": _check_harnesses,
    "repos.yaml": _check_repos,
    "intake.yaml": _check_intake,
    "access.yaml": _check_access,
    "theme.yaml": _check_theme,
    "notify.yaml": _check_notify,
    "plugins.yaml": _check_plugins,
    "plugins.lock": _check_lock,
}
