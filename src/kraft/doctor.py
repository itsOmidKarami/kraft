"""`kraft admin doctor`: the checks a human runs when Kraft is misbehaving.

One pass, one line per check, no `--fix` — doctor reports and the human decides.

Every check here answers a question someone actually asked once. That bar is the
only thing that stops a doctor command from growing output nobody reads
(spec E §4), so a new check belongs here after it has been wanted, not before.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import yaml

from kraft import auth, capabilities, client, config, harness
from kraft.adapters import forge
from kraft.executor import fallback
from kraft.paths import (
    BUNDLED,
    RunDirs,
    default_run_dir,
    default_skills_dir,
    default_templates_dir,
)
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError, Workspace
from kraft.templates.library import CHAINS_DIR, TemplateLibrary, TemplateLibraryError
from kraft.templates.models import AgentTask, ForgeTask
from kraft.worker import sandbox
from kraft.worker import steering as steering_mod

#: The work-graph CLI. Optional by design (Kraft-7gy): intake files a work item
#: with no bead when it is absent, and nothing else about an item needs one.
BEADS_COMMAND = "bd"


def _check(
    name: str, ok: bool, detail: str = "", *, skipped: bool = False, warn: bool = False
) -> dict:
    """A skipped check is `ok`: it did not fail, it did not run. Only a real
    failure may set the exit code, or one dead server reads as five problems.

    `warn` is also `ok` for the same reason, but it is not a skip: the check
    ran and could not confirm the answer (a release feed that didn't
    respond, say), which is worth a human noticing even though `doctor &&
    deploy` must not break on it. A skip and a warn look the same to the exit
    code and different to the human reading the output.
    """
    return {"name": name, "ok": ok, "detail": detail, "skipped": skipped, "warn": warn}


async def run_checks() -> list[dict]:
    """Every check, in the order a human debugs: is it up, is it healthy, is it
    configured, can it launch an agent, is its state on disk still coherent."""
    checks: list[dict] = []
    try:
        health = await client.health()
        checks.append(_check("server", True, client.base_url()))
    except ValueError as exc:
        health = None
        checks.append(_check("server", False, str(exc)))

    checks.extend(
        _health_checks(health)
        if health is not None
        else [_check("health", True, "skipped: no server", skipped=True)]
    )
    checks.extend(_config_checks())
    checks.append(_pidfile_check())
    checks.extend(_agent_checks())
    checks.append(await _mcp_check(health is not None))
    checks.append(_path_check())
    checks.append(_completion_check())
    checks.append(_bundle_check())
    checks.append(_version_check())
    checks.append(_beads_check())
    if health is None:
        checks.append(_check("repos", True, "skipped: no server", skipped=True))
        checks.append(_check("worktrees", True, "skipped: no server", skipped=True))
    else:
        checks.extend(await _repo_checks())
        checks.append(await _orphan_check())
    return checks


def _health_checks(payload: dict) -> list[dict]:
    """`/health`, one line per degraded reason.

    A single line reading "degraded" sends you to the browser anyway, which is
    the thing doctor exists to avoid.
    """
    index = payload.get("index") or {}
    embeddings = index.get("embeddings") or {}
    reasons = [
        f"invalid template {name}: {why}"
        for name, why in (payload.get("invalid_templates") or {}).items()
    ]
    if payload.get("invalid_policy"):
        reasons.append(f"invalid policy: {payload['invalid_policy']}")
    reasons.extend(f"index: {error}" for error in index.get("errors") or [])
    orphaned = (payload.get("reattach_summary") or {}).get("unknown") or []
    if orphaned:
        reasons.append(f"orphaned agent sessions: {', '.join(orphaned)}")
    # Semantic search is an opt-in extra (`available` is whether it imports),
    # so /health stays `ok` without it and so does doctor: an ok line carrying
    # the advice, never a FAIL on something the operator never asked for
    # (Kraft-rj8cn). Installed but failing to load or encode is a FAIL: the
    # operator did ask for it (Kraft-pm2rj).
    if not embeddings.get("available"):
        extra = _check(
            "embeddings", True, f"not installed: {embeddings.get('reason') or 'no reason given'}"
        )
    elif embeddings.get("reason"):
        extra = _check("embeddings", False, f"installed but broken: {embeddings['reason']}")
    else:
        extra = _check("embeddings", True, f"available ({embeddings.get('model')})")
    if not reasons:
        status = str(payload.get("status", "?"))
        return [_check("health", payload.get("status") == "ok", status), extra]
    return [*(_check("health", False, reason) for reason in reasons), extra]


def _config_checks() -> list[dict]:
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if not templates.is_dir():
        # Every row still reports, as a skip: every check this function can emit
        # emits on every path, so a caller reading the run by name never has to
        # ask whether a row is missing because it passed, because it was skipped,
        # or because an earlier branch returned before reaching it.
        return [
            _check(
                "templates", False, f"{templates} does not exist — start `kraft` once to seed it"
            ),
            _chain_templates_check(),
            _check("chains", True, "skipped: no templates dir", skipped=True),
            _capabilities_check(),
            _token_check(),
        ]
    checks = [_check("templates", True, str(templates))]
    try:
        config.Access.load(templates / "access.yaml")
        checks.append(_check("access.yaml", True, "parses"))
    except config.ConfigError as exc:
        checks.append(_check("access.yaml", False, str(exc)))
    checks.append(_chain_templates_check())
    checks.append(_chains_check(templates))
    checks.append(_capabilities_check())
    checks.append(_token_check())
    return checks


def _pidfile_check() -> dict:
    """A pidfile naming a process that is gone looks, to every other check
    here, exactly like no server ever started -- nothing before this ever
    said so (Kraft-mqwg)."""
    pid_path = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).pid
    if not pid_path.is_file():
        return _check("pidfile", True, "no pidfile")
    try:
        pid = int(pid_path.read_text())
    except (OSError, ValueError) as exc:
        return _check("pidfile", False, f"{pid_path}: {exc}")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return _check(
            "pidfile",
            False,
            f"{pid_path} names pid {pid}, which is not running - stale; "
            "the next `kraft admin start` clears it",
        )
    except PermissionError:
        pass  # alive, and not ours to signal
    return _check("pidfile", True, f"{pid_path} (pid {pid})")


def _chain_template_files(directory: Path) -> dict[str, set[str]]:
    """Every chain file under `directory/chains/` -- one with a top-level
    `nodes` list -- mapped to the ids of its nodes. By structure, not by
    filename, so a chain added in a later version is picked up without a code
    change here. Authored node ids: a chain's nodes are listed in the chain
    file itself even when a node `extends` a library node."""
    found: dict[str, set[str]] = {}
    chains = directory / CHAINS_DIR
    if not chains.is_dir():
        return found
    for path in sorted(chains.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except OSError, ValueError, yaml.YAMLError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            continue
        found[path.name] = {
            node["id"] for node in data["nodes"] if isinstance(node, dict) and "id" in node
        }
    return found


def _chain_templates_check() -> dict:
    """Nodes this version's chain templates ship, missing from the live
    installed copy. `templates/` is seeded once and never
    overwritten (`cli.seed_home`), so a template shipped or changed after an
    operator's copy was seeded keeps missing the new node forever with
    nothing to say so.

    Always `ok`: which nodes a repo's chain actually runs is an operator's own
    edit, not a fault.
    """
    shipped_dir = BUNDLED / "templates"
    if not (shipped_dir / CHAINS_DIR).is_dir():
        return _check("chain_templates", True, "skipped: not an installed Kraft", skipped=True)
    shipped = _chain_template_files(shipped_dir)
    if not shipped:
        return _check(
            "chain_templates", True, "skipped: no chain templates in this version", skipped=True
        )
    live_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    live = _chain_template_files(live_dir)
    parts = []
    for name, shipped_ids in shipped.items():
        live_ids = live.get(name)
        if live_ids is None:
            parts.append(f"{name} missing entirely")
            continue
        absent = sorted(shipped_ids - live_ids)
        if absent:
            parts.append(f"{name}: {', '.join(absent)} missing")
    if not parts:
        return _check("chain_templates", True, f"{len(shipped)} template(s), no missing nodes")
    return _check(
        "chain_templates",
        True,
        f"{'; '.join(parts)} in {live_dir}, but shipped in this version's defaults — "
        "Settings → Chains, or edit those files",
    )


def _chains_check(templates: Path) -> dict:
    """Every chain of the live library that does not resolve, by id and with
    the resolver's own message (Kraft-n1zp9) -- `admin templates lint`'s pass,
    read from disk so it answers with no server running. A chain that does
    not resolve otherwise only shows up as the next intake on it failing."""
    skills = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    report = TemplateLibrary.lint_dir(templates, skills_dir=skills)
    if report.valid:
        return _check("chains", True, f"{len(report.chains)} chain(s) resolve")
    return _check("chains", False, "; ".join(str(issue) for issue in report.issues))


def _capabilities_check() -> dict:
    """What this version can do that the live seeded config cannot (Kraft-hxt6x).

    Always `ok`, like `_chain_templates_check`: whether to
    adopt a capability is an operator's decision, not a fault. The row exists
    because the alternative is silence -- `templates/` is seeded once and never
    overwritten, and on a real install two capabilities shipped within a week
    were both inactive with nothing anywhere saying so.

    Reports adoption instructions, never a diff and never an applied change:
    the live library carries per-task `model`/`effort` choices the shipped
    defaults do not, so overwriting destroys operator intent.
    """
    live_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    stamp_path = live_dir / ".seeded-version"
    try:
        stamp = stamp_path.read_text().strip() or None
    except OSError:
        # Seeded before stamping existed, or unreadable. Either way the home
        # knows nothing about itself; `added_since(None)` says everything.
        stamp = None
    missing = capabilities.added_since(stamp)
    seeded = f"seeded at {stamp}" if stamp else "seeded before versions were recorded"
    if not missing:
        return _check("capabilities", True, f"{seeded}; up to date")
    lines = [f"{seeded}; {len(missing)} capability(ies) added since"]
    for c in missing:
        lines.append(f"  {c.version}  {c.name}: {c.what}")
        lines.append(f"      -> {c.how}")
    return _check("capabilities", True, "\n".join(lines))


def _token_check() -> dict:
    """The MCP bearer is a local credential: every other user on the machine can
    drive Kraft with a copy of it, so its mode is part of whether Kraft is well."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    path = run_dir / auth.MCP_TOKEN_FILE
    if not auth.read_mcp_token(run_dir):
        return _check(
            "mcp token", False, f"missing or empty at {path} — the server writes it on start"
        )
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        return _check("mcp token", False, f"{path}: {exc}")
    if mode & 0o077:
        return _check(
            "mcp token", False, f"{path} is {mode:04o}, not 0600 — any local user can read it"
        )
    return _check("mcp token", True, f"{path} ({mode:04o})")


def _agent_checks() -> list[dict]:
    """One PATH check per harness profile the live library's chains select,
    plus one failure row per harness file or profile table that failed to load.

    Read from disk, the same `KRAFT_TEMPLATES_DIR` precedence dispatch reads
    `harnesses.yaml` by (`agent.harness_profile`). A chain that does not resolve
    selects nothing here: `/health` already names the library error, and a
    second copy of it helps nobody. A shipped profile nothing selects is not a
    missing dependency and gets no row.
    """
    live = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    harnesses = harness.load(None)
    checks: list[dict] = []
    try:
        table = HarnessProfileTable.from_yaml(live / "harnesses.yaml", harnesses=harnesses.valid)
    except TemplateEnvironmentError as exc:
        checks.append(_check("harnesses.yaml", False, str(exc)))
        table = HarnessProfileTable(profiles={})
    profiles = table.profiles
    for pid in sorted(_selected_profiles(live)):
        profile = profiles.get(pid)
        if profile is None:
            checks.append(_check(f"agent: {pid}", False, "no such profile in harnesses.yaml"))
            continue
        provider = harnesses.valid.get(profile.provider)
        if provider is None:
            checks.append(
                _check(
                    f"agent: {pid}",
                    False,
                    harnesses.invalid.get(profile.provider, f"{profile.provider}: not found"),
                )
            )
            continue
        exe = profile.executable or provider.command[0]
        found = shutil.which(exe)
        if not found:
            checks.append(
                _check(f"agent: {pid}", False, f"`{exe}` is not on PATH — no chain can run")
            )
            continue
        real = os.path.realpath(found)
        # Passing loudly on the fake is the point: a dev instance that looks
        # like it is working is spending no tokens on purpose, and that is
        # worth reading once before you wonder why nothing real happened.
        detail = (
            f"{found} -> {real} (the dev fake: it spends no tokens)"
            if "fixtures" in Path(real).parts
            else found
        )
        checks.append(_check(f"agent: {pid}", True, detail))
    for hid, reason in sorted(harnesses.invalid.items()):
        checks.append(_check(f"harness: {hid}", False, reason))
    return checks + _pairing_checks(live, table, harnesses)


def _pairing_checks(live: Path, table: HarnessProfileTable, harnesses) -> list[dict]:
    """One failure per agent profile a task selects and the launch would
    refuse on that task's harness (Kraft-ps1ao), naming every such task in the
    launch's words. A harness that does not
    resolve already has its own row above."""
    refused: dict[str, list[str]] = {}
    for chain in _resolved_chains(live):
        for node in chain.nodes:
            for t in node.tasks():
                if not isinstance(t.task, AgentTask):
                    continue
                # The task's own launch, then each fallback entry (Kraft-0a3h8).
                entries, source = fallback.fallback_list(t.task, table)
                launches = [(t.task, "")] + [
                    (fallback.apply(t.task, e), f" fallback entry {n} ({source})")
                    for n, e in enumerate(entries)
                ]
                for task, at in launches:
                    harness_profile = table.profiles.get(task.harness)
                    if (
                        task.profile is None
                        or harness_profile is None
                        or harness_profile.provider not in harnesses.valid
                    ):
                        continue
                    why = table.pairing_problem(task.profile, harness_profile, harnesses.valid)
                    if why:
                        refused.setdefault(task.profile, []).append(
                            f"chain {chain.id!r} task {t.path!r}{at}: {why}"
                        )
    return [_check(f"profile: {p}", False, "; ".join(why)) for p, why in sorted(refused.items())]


def _resolved_chains(live: Path) -> list:
    """Every chain of the live library that resolves; `[]` when the library
    itself does not load."""
    try:
        library = TemplateLibrary.from_yaml_dir(live)
    except TemplateLibraryError:
        return []
    chains = []
    for id in library.chain_ids:
        try:
            chains.append(library.resolve_chain(id))
        except TemplateLibraryError:
            continue
    return chains


def _selected_profiles(live: Path) -> set[str]:
    return {
        t.task.harness
        for chain in _resolved_chains(live)
        for node in chain.nodes
        for t in node.tasks()
        if isinstance(t.task, AgentTask)
    }


#: Where `claude mcp add --scope user` records its servers -- what
#: `init.install` runs for a user-scope install (init.py:122).
CLAUDE_USER_CONFIG = ".claude.json"


def _names_kraft(path: Path) -> bool:
    """Does this agent config register a server called `kraft`?

    The file is read directly rather than shelling out to `claude mcp list`: the
    subprocess's output format is not ours to depend on, and the file it reads
    is right there.
    """
    try:
        return "kraft" in (json.loads(path.read_text()).get("mcpServers") or {})
    except OSError, ValueError, AttributeError:
        return False


async def _mcp_check(server_up: bool) -> dict:
    """Is the Kraft MCP server registered with the agent CLI?

    Next to `_agent_checks` because it answers the same question: can this
    machine actually launch a worker. Every launch passes
    `--permission-prompt-tool mcp__kraft__permission_request`
    (`adapters/agent.py`), and on an install where `kraft admin init` was never
    run that tool does not exist -- the CLI exits 0 and ignores the flag, and
    permission asks go silently unanswered with nothing reporting it.

    `ok=False`, unlike `bd` or shell completion: those are choices an operator
    made, this one silently breaks a chain that is already running.

    Rejected, as the bead records: passing `--mcp-config` on every launch, which
    would make Kraft write the operator's agent config.
    """
    user = Path.home() / CLAUDE_USER_CONFIG
    if _names_kraft(user):
        return _check("mcp server", True, f"registered in {user}")
    if not server_up:
        # The repo-scope half needs `GET /repos`, like every other
        # server-dependent check here.
        return _check("mcp server", True, "skipped: no server", skipped=True)
    for repo in await client.repos():
        path = Path(repo["path"]) / ".mcp.json"
        if _names_kraft(path):
            return _check("mcp server", True, f"registered in {path}")
    return _check(
        "mcp server",
        False,
        "no kraft MCP server registered — workers' permission prompts go unanswered; "
        "run `kraft admin init`",
    )


def _path_check() -> dict:
    """Is the `kraft` on PATH this one? Every MCP registration runs it by name,
    so an older install ahead of this one on PATH answers every agent's tool
    call with that install's code, whatever `kraft admin update` installed.
    `ok=False` for the same reason as `_mcp_check`: it breaks silently."""
    from kraft import update

    other = update.shadowing_kraft()
    if other is None:
        return _check("kraft on PATH", True, shutil.which("kraft") or "not on PATH")
    return _check(
        "kraft on PATH",
        False,
        f"{other} is another install, ahead of this one ({update.installed()}, "
        f"{sys.prefix}) -- MCP servers and hooks run it; uninstall it or reorder PATH",
    )


def _beads_check() -> dict:
    """Always `ok`: `_check(ok=False)` sets `kraft admin doctor`'s exit code,
    and an install with no work-graph tracker is a choice, not a fault (Kraft-7gy)."""
    found = shutil.which(BEADS_COMMAND)
    if not found:
        return _check(
            "bd", True, f"`{BEADS_COMMAND}` not installed — work items will run without beads"
        )
    return _check("bd", True, found)


def _completion_check() -> dict:
    """Tab completion is convenience, not health: always `ok` -- a missing
    `eval` line is a line to add, not a reason to exit 1."""
    shell = os.environ.get("SHELL", "")
    if "zsh" not in shell:
        return _check("shell completion", True, "skipped: not zsh", skipped=True)
    rc = Path.home() / ".zshrc"
    try:
        registered = "register-python-argcomplete kraft" in rc.read_text()
    except OSError:
        registered = False
    if registered:
        return _check("shell completion", True, f"registered in {rc}")
    return _check(
        "shell completion",
        True,
        f'not registered — add eval "$(register-python-argcomplete kraft)" to {rc}',
    )


def _runs_forge_tasks() -> bool:
    """Does any chain of the live library run a forge task? Every V1 forge task
    picks its backend from the repo's recorded `forge` (`backend: auto`), so
    one forge task anywhere means every connected repo needs a reachable forge.
    A library that cannot be read means no forge checks: `/health` already
    reports that failure, and a second copy of it per repo helps nobody.
    """
    live = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    return any(
        isinstance(t.task, ForgeTask)
        for chain in _resolved_chains(live)
        for node in chain.nodes
        for t in node.tasks()
    )


def _forge_check(repo: config.RepoEntry) -> dict:
    """Can this repo's `backend: auto` forge nodes actually run?

    Fails rather than reporting with detail: a chain that runs a forge task
    means the operator intends to run it, and the alternative is finding out
    three nodes into a work item.
    """
    name = f"forge {_label(repo)}"
    try:
        # The same call `run_task` makes, so doctor and the runtime cannot
        # disagree about either the answer or the wording of the failure.
        cli = forge.backend_for("auto", repo.forge)
    except forge.ForgeError as exc:
        return _check(name, False, str(exc))
    if cli == "fake":
        return _check(name, True, "fake · dev only: opens, merges and pushes nothing", warn=True)
    if not shutil.which(cli):
        return _check(name, False, f"`{cli}` is not on PATH — the forge nodes cannot run")
    return _check(name, True, f"{repo.forge} · {cli}")


def _label(repo: config.RepoEntry) -> str:
    return repo.name or repo.path


async def _repo_checks() -> list[dict]:
    """Through `GET /repos`, not `repos.yaml`: spec D §4 keeps one reader of the
    repo list on this side of the wire, and doctor is not an exception to it."""
    checks = []
    # Read once, not per repo: whether any chain runs a forge task is a fact
    # about the install, and every repo is measured against the same answer.
    auto = _runs_forge_tasks()
    # Read once too: the live library's steering profiles, name to
    # instructions, the same shape `api.deps.library_steering` hands
    # `steering.select`. `None` when the library itself does not load --
    # `_chains_check` already reports that failure.
    live = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    profiles = _library_steering(live)
    # Retyped from the wire: the checks below read the entry the loader
    # models, not a dict whose keys each one must spell right. The loader's
    # own unrecognised-key warning stays quiet: doctor fails a row on them.
    repos = [
        config.RepoEntry.model_validate(r, context={"unrecognised_keys_reported": True})
        for r in await client.repos()
    ]
    # Kraft-dshto: `GET /repos` lists a sandboxed workspace rather than
    # refusing it, so the repository that sets the sandbox fails its own row.
    sandboxed = {
        rid: ws_id
        for ws_id, ws in (await client.workspaces()).items()
        for rid in config.sandboxed_members(Workspace.model_validate(ws), repos)
    }
    for repo in repos:
        path = Path(repo.path)
        name = f"repo {_label(repo)}"
        if not path.is_dir():
            checks.append(_check(name, False, f"{path} no longer exists"))
        elif not (path / ".git").exists():
            # a file in a linked worktree, a directory in a normal clone
            checks.append(_check(name, False, f"{path} is no longer a git repo"))
        elif not (path / ".beads").is_dir():
            # Not a failure either: work items here simply file no bead.
            checks.append(_check(name, True, f"{path} — no .beads: work items here file no bead"))
        else:
            checks.append(_check(name, True, str(path)))
        if repo.setup_command is None:
            # No default stands behind this key: an undeclared repo stops its
            # next work item when the worktree is built (Kraft-kji8w). That is
            # deliberate; being told here rather than by a parked item is what
            # makes it survivable.
            suggestion = config._first_setup_command(path) if path.is_dir() else None
            checks.append(
                _check(
                    f"setup {_label(repo)}",
                    False,
                    f"no setup_command in repos.yaml — suggest: {suggestion or 'none found'}"
                    ' (use "" if this repo deliberately needs no preparation)',
                )
            )
        unrecognised = config.unrecognised_repo_keys(repo.model_extra or {})
        if unrecognised:
            # Loaded, with a warning nobody may be reading: a key that binds
            # nothing is exactly what a typo looks like (Kraft-4hn34).
            checks.append(
                _check(
                    f"keys {_label(repo)}",
                    False,
                    f"repos.yaml keys nothing reads: {', '.join(unrecognised)} -- remove them",
                )
            )
        if repo.id in sandboxed:
            checks.append(
                _check(
                    f"sandbox {_label(repo)}",
                    False,
                    sandbox.submodule_refusal(
                        f"workspaces.{sandboxed[repo.id]}: repository {repo.id!r}"
                    ),
                )
            )
        if auto:
            checks.append(_forge_check(repo))
        if repo.steering and profiles is not None:
            checks.append(_steering_check(repo, profiles))
    return checks or [_check("repos", True, "none connected")]


def _library_steering(live: Path) -> dict[str, str] | None:
    """The live library's steering profiles, name to instructions, the shape
    `steering.select` reads; `None` when the library itself does not load --
    `_chains_check` already reports that failure, and a second copy of it per
    repo helps nobody."""
    try:
        library = TemplateLibrary.from_yaml_dir(live)
    except TemplateLibraryError:
        return None
    return {name: profile.instructions for name, profile in library.steering.items()}


def _steering_check(repo: config.RepoEntry, profiles: dict[str, str]) -> dict:
    """Kraft-v7u1f: a repos.yaml `steering:` name the library does not
    define. Repo save, intake, and a library save that removes a named
    profile all already refuse this; doctor had no row for it, so a
    hand-edited repos.yaml (or a library edited outside Kraft) read healthy
    until the next intake's 422. Reuses `steering.select`, the same
    resolution those refusals use, so doctor cannot disagree with them about
    what counts as missing."""
    name = f"steering {_label(repo)}"
    try:
        steering_mod.select(repo.steering, profiles, where=f"repos.yaml: {repo.path}")
    except steering_mod.SteeringError as exc:
        return _check(name, False, str(exc))
    return _check(name, True, ", ".join(repo.steering))


async def _orphan_check() -> dict:
    """Worktrees with no work item row. Read-only, like every check here: which
    of them is safe to delete is a judgement about uncommitted work."""
    worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
    if not worktrees.is_dir():
        return _check("worktrees", True, f"{worktrees} does not exist yet")
    known = {item["id"] for item in await client.list_work_items()}
    try:
        orphans = sorted(d.name for d in worktrees.iterdir() if d.is_dir() and d.name not in known)
    except OSError as exc:
        return _check("worktrees", False, f"{worktrees}: {exc}")
    if orphans:
        return _check(
            "worktrees",
            False,
            f"{len(orphans)} under {worktrees} with no work item: {', '.join(orphans)}",
        )
    return _check("worktrees", True, str(worktrees))


def _bundle_check() -> dict:
    """Is the built SPA actually in this install?

    `just bundle` is what puts it there. A wheel built by anything else matches
    the `_bundled/**/*` package-data glob against nothing, and the result is an
    API that serves JSON and no UI - visible only to whoever opens the browser.
    """
    index = BUNDLED / "web" / "index.html"
    if index.is_file():
        return _check("spa bundle", True, str(index.parent))
    return _check("spa bundle", False, f"no SPA at {index} - built without `just bundle`")


def _version_check() -> dict:
    """Informational, and `ok` even when behind.

    `doctor` exits 1 on any failed check, and a release landing must not start
    failing somebody's `kraft admin doctor && deploy`. Being out of date is a
    thing to know, not a thing that is broken.
    """
    from kraft import update

    here = update.installed()
    if os.environ.get("KRAFT_NO_UPDATE_CHECK"):
        # The same switch `cli.admin._update_notice` honours: one env var turns off
        # every version check, so an air-gapped machine never reaches for the
        # network and a test suite never depends on gitlab.com being up.
        return _check("version", True, f"{here} (skipped: KRAFT_NO_UPDATE_CHECK)", skipped=True)
    release = update.latest(channel=update.channel_of(update.installed()))
    if release is None:
        # Not a skip: this ran and failed to get an answer, which on a
        # private project usually means no GITLAB_TOKEN and no authenticated
        # `glab` - worth a human's attention, unlike an opt-out or a missing
        # server.
        return _check("version", True, f"{here} (could not reach the release feed)", warn=True)
    if update.is_behind(release):
        return _check("version", True, f"{here} installed, {release.tag} available")
    return _check("version", True, f"{here} (the newest release)")
