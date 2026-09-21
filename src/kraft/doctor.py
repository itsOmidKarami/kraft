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
from pathlib import Path

import yaml

from kraft import auth, capabilities, client, config, harness, templates
from kraft.adapters import forge
from kraft.paths import BUNDLED, RunDirs, default_run_dir, default_templates_dir

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
    checks.extend(_agent_checks(*_loaded_registry()))
    checks.append(await _mcp_check(health is not None))
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
    if not embeddings.get("available"):
        reasons.append(f"embeddings unavailable: {embeddings.get('reason') or 'no reason given'}")
    orphaned = (payload.get("reattach_summary") or {}).get("unknown") or []
    if orphaned:
        reasons.append(f"orphaned agent sessions: {', '.join(orphaned)}")
    if not reasons:
        return [_check("health", payload.get("status") == "ok", str(payload.get("status", "?")))]
    return [_check("health", False, reason) for reason in reasons]


def _config_checks() -> list[dict]:
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if not templates.is_dir():
        # `hooks` still reports, as a skip: every check this function can emit
        # emits on every path, so a caller reading the run by name never has to
        # ask whether a row is missing because it passed, because it was skipped,
        # or because an earlier branch returned before reaching it.
        return [
            _check(
                "templates", False, f"{templates} does not exist — start `kraft` once to seed it"
            ),
            _hooks_check(),
            _dead_hooks_check(),
            _chain_templates_check(),
            _capabilities_check(),
            _token_check(),
        ]
    checks = [_check("templates", True, str(templates))]
    try:
        config.load_access(templates / "access.yaml")
        checks.append(_check("access.yaml", True, "parses"))
    except config.ConfigError as exc:
        checks.append(_check("access.yaml", False, str(exc)))
    checks.append(_hooks_check())
    checks.append(_dead_hooks_check())
    checks.append(_chain_templates_check())
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


def _is_noop(binding) -> bool:
    return isinstance(binding, dict) and binding.get("handler") == "noop"


def _hooks_check() -> dict:
    """Hooks this version ships a real binding for, still on `builtin: noop`
    locally.

    `templates/` is seeded once and never overwritten (`cli.seed_home`), so an
    operator who installed before a hook was implemented keeps the placeholder
    forever — and a placeholder gate shows an empty card with nothing to read
    and no reason why. Always `ok`: which hooks to run is the operator's
    decision, and `kraft admin doctor` exits 1 on any failed check.
    """
    shipped_path = BUNDLED / "templates" / "registry.yaml"
    # `KRAFT_TEMPLATES_DIR` first, exactly as `_config_checks` reads it above:
    # doctor must inspect the directory the running server actually loaded,
    # not the one $KRAFT_HOME implies.
    live_path = (
        Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir()) / "registry.yaml"
    )
    if not shipped_path.is_file():
        # A source checkout has no `_bundled/`: there is nothing to compare to.
        return _check("hooks", True, "skipped: not an installed Kraft", skipped=True)
    try:
        shipped = yaml.safe_load(shipped_path.read_text())["hooks"]
        live = yaml.safe_load(live_path.read_text())["hooks"]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        return _check("hooks", True, f"skipped: cannot compare ({exc})", skipped=True)
    # Two different problems with two different fixes, so they are counted
    # separately rather than as one "stale" bucket: a hook still on the
    # placeholder is a binding to change, while a hook missing entirely is a
    # line to add. The second is what a registry seeded before the hook point
    # existed looks like, and `.get(h)` returning None reads as "not a noop" —
    # which silently exempted exactly the case this check exists for (Kraft-zmb).
    real = {h: b for h, b in shipped.items() if not _is_noop(b)}
    placeholder = sorted(h for h in real if _is_noop(live.get(h)))
    absent = sorted(h for h in real if h not in live)
    if not placeholder and not absent:
        return _check("hooks", True, f"{len(live)} bound, none left on the placeholder")
    parts = []
    if placeholder:
        parts.append(f"{', '.join(placeholder)} still builtin:noop")
    if absent:
        parts.append(f"{', '.join(absent)} missing entirely")
    return _check(
        "hooks",
        True,
        f"{'; '.join(parts)} in {live_path}, but bound in this version's "
        "defaults — Settings → Hooks, or edit that file",
    )


def _chain_template_hooks(directory: Path) -> set[str]:
    """Every hook named in any chain template's node `tasks`, across every YAML
    file in `directory` with a `nodes` list -- the same file shape
    `_chain_template_files` walks, read for hook names instead of node ids.
    """
    hooks: set[str] = set()
    if not directory.is_dir():
        return hooks
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except OSError, ValueError, yaml.YAMLError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            continue
        for node in data["nodes"]:
            if isinstance(node, dict) and isinstance(node.get("tasks"), list):
                hooks.update(t for t in node["tasks"] if isinstance(t, str))
    return hooks


def _dead_hooks_check() -> dict:
    """Hooks a chain template actually dispatches, bound to `builtin: noop` in
    the live registry, and either `builtin: noop` or absent entirely in this
    version's shipped registry — dead by design, not by staleness.

    A different question from `_hooks_check`, which reports a hook real (not
    noop) in the shipped registry but noop-or-absent in the live one: a
    registry that has fallen behind what this version ships. `on.review.mr.run`
    is `builtin: noop` in the *shipped* registry too, so it never enters
    `_hooks_check`'s `real` set and can never be reported there (spec "Why the
    existing doctor check cannot catch C6").

    Reported as information, not a defect. A core noop is a plugin extension
    point by design: `on.review.mr.run` ships noop so a plugin can fill it
    (kraft-lite does). A plugin fills it by rebinding it in the live registry,
    so a hook still noop in live is one that no plugin binds on this install.
    The operator should know that tier does nothing here. It is not broken.

    Scoped to noop-or-absent in shipped so the two checks are disjoint by
    construction: `_hooks_check` only ever selects a hook that is real (not
    noop) in shipped, this one only ever selects one that is noop or absent
    in shipped, so no hook can satisfy both and be reported twice for two
    different reasons.

    Always `ok`, like `_hooks_check`: which hooks a chain names is the
    operator's own template, not a fault doctor should fail the exit code over.
    """
    shipped_path = BUNDLED / "templates" / "registry.yaml"
    if not shipped_path.is_file():
        return _check("dead_hooks", True, "skipped: not an installed Kraft", skipped=True)
    live_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    live_path = live_dir / "registry.yaml"
    try:
        shipped = yaml.safe_load(shipped_path.read_text())["hooks"]
        live = yaml.safe_load(live_path.read_text())["hooks"]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        return _check("dead_hooks", True, f"skipped: cannot compare ({exc})", skipped=True)
    dispatched = _chain_template_hooks(live_dir)
    dead = sorted(
        h
        for h in dispatched
        if (h not in shipped or _is_noop(shipped.get(h))) and _is_noop(live.get(h))
    )
    if not dead:
        return _check("dead_hooks", True, f"{len(dispatched)} hook(s) dispatched, none dead")
    return _check(
        "dead_hooks",
        True,
        f"{', '.join(dead)} named in a chain's tasks but builtin:noop in shipped and "
        "live registries — this tier does nothing on this install unless a plugin binds it",
    )


def _chain_template_files(directory: Path) -> dict[str, set[str]]:
    """Every YAML file in `directory` that is a chain template — has a
    top-level `nodes` list — mapped to the ids of its nodes.

    Distinguishes chain templates (`default.yaml`, `quick-task.yaml`) from
    the registry and from config with no chain shape at all (`intake.yaml`,
    `policy.yaml`, `repos.yaml`) by structure, not by filename, so a
    template added in a later version is picked up without a code change
    here.

    Narrow margin worth knowing: the V1 `library.yaml` *does* have a top-level
    `nodes:`. It is excluded only because that `nodes:` is a mapping of reusable
    nodes by name and the guard below is `isinstance(..., list)`. A V1 file that
    ever grew a top-level `nodes` list would be read as a chain template here.
    The V1 chains themselves are under `chains/`, which this glob does not
    descend into.
    """
    found: dict[str, set[str]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.yaml")):
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
    installed copy — the same drift `_hooks_check` catches for hooks,
    generalized to node lists. `templates/` is seeded once and never
    overwritten (`cli.seed_home`), so a template shipped or changed after an
    operator's copy was seeded keeps missing the new node forever with
    nothing to say so.

    Always `ok`, like `_hooks_check`: which nodes a repo's chain actually
    runs is an operator's own edit, not a fault.
    """
    shipped_dir = BUNDLED / "templates"
    if not shipped_dir.is_dir():
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


def _capabilities_check() -> dict:
    """What this version can do that the live seeded config cannot (Kraft-hxt6x).

    Always `ok`, like `_hooks_check` and `_chain_templates_check`: whether to
    adopt a capability is an operator's decision, not a fault. The row exists
    because the alternative is silence -- `templates/` is seeded once and never
    overwritten, and on a real install two capabilities shipped within a week
    were both inactive with nothing anywhere saying so.

    Reports adoption instructions, never a diff and never an applied change:
    the live registry carries per-hook `model`/`effort` choices the shipped
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


def _loaded_registry() -> tuple[templates.Registry, harness.HarnessSet]:
    """The live registry and harness set, for `_agent_checks` -- the same
    `KRAFT_TEMPLATES_DIR` precedence `_hooks_check` reads by. A registry that
    fails to load is reported as no agent bindings at all rather than
    crashing doctor: some other check names the real problem."""
    hs = harness.load(None)
    live_path = (
        Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir()) / "registry.yaml"
    )
    try:
        reg = templates.load_registry(live_path, harnesses=hs)
    except templates.RegistryError:
        reg = templates.Registry(hooks={})
    return reg, hs


def _agent_checks(registry: templates.Registry, harnesses: harness.HarnessSet) -> list[dict]:
    """One PATH check per harness the live registry actually names, plus one
    failure row per harness file that failed to load.

    Every existing install ships one harness (claude), so this used to be a
    single hardcoded check (`AGENT_COMMAND`) — a chain with a codex node must
    be told about codex, not reassured about claude. A shipped harness
    nothing references is not a missing dependency and gets no row.
    """
    checks: list[dict] = []
    referenced = sorted(
        {
            b["harness"]
            for b in registry.hooks.values()
            if b.get("kind") == "agent" and b.get("harness")
        }
    )
    for hid in referenced:
        h = harnesses.valid.get(hid)
        if h is None:
            checks.append(_check(f"agent: {hid}", False, harnesses.invalid.get(hid, "not found")))
            continue
        # argv[0] of the harness's own prefix; a binding may override it per
        # hook, and that override is checked with the binding, not here.
        exe = h.command[0]
        found = shutil.which(exe)
        if not found:
            checks.append(
                _check(f"agent: {hid}", False, f"`{exe}` is not on PATH — no chain can run")
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
        checks.append(_check(f"agent: {hid}", True, detail))
    for hid, reason in sorted(harnesses.invalid.items()):
        checks.append(_check(f"harness: {hid}", False, reason))
    return checks


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
    """Tab completion is convenience, not health: always `ok`, like `_hooks_check`
    — a missing `eval` line is a line to add, not a reason to exit 1."""
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


def _binds_auto_forge() -> bool:
    """Does the registry the server actually loaded bind a forge hook to `auto`?

    `KRAFT_TEMPLATES_DIR` first, exactly as `_hooks_check` reads it. A registry
    that cannot be read means no forge checks: `templates` and `hooks` already
    report that failure, and a second copy of it per repo helps nobody.
    """
    live = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir()) / "registry.yaml"
    try:
        hooks = yaml.safe_load(live.read_text())["hooks"]
    except OSError, ValueError, KeyError, TypeError, yaml.YAMLError:
        return False
    return any(
        isinstance(b, dict) and b.get("kind") == "forge" and b.get("backend") == "auto"
        for b in hooks.values()
    )


def _forge_check(repo: dict) -> dict:
    """Can this repo's `backend: auto` forge nodes actually run?

    Fails rather than reporting with detail, unlike `_hooks_check`: an operator
    who does not want these hooks has bound them to something else, so `auto`
    in the live registry means they intend to run them — and the alternative is
    finding out three nodes into a work item.
    """
    name = f"forge {repo.get('name') or repo['path']}"
    try:
        # The same call `run_task` makes, so doctor and the runtime cannot
        # disagree about either the answer or the wording of the failure.
        cli = forge.backend_for("auto", repo.get("forge"))
    except forge.ForgeError as exc:
        return _check(name, False, str(exc))
    if cli == "fake":
        return _check(name, True, "fake · in-process, dev only")
    if not shutil.which(cli):
        return _check(name, False, f"`{cli}` is not on PATH — the forge nodes cannot run")
    return _check(name, True, f"{repo['forge']} · {cli}")


async def _repo_checks() -> list[dict]:
    """Through `GET /repos`, not `repos.yaml`: spec D §4 keeps one reader of the
    repo list on this side of the wire, and doctor is not an exception to it."""
    checks = []
    # Read once, not per repo: which backend the hooks are bound to is a fact
    # about the install, and every repo is measured against the same answer.
    auto = _binds_auto_forge()
    for repo in await client.repos():
        path = Path(repo["path"])
        name = f"repo {repo.get('name') or repo['path']}"
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
        if repo.get("setup_command") is None:
            # No default stands behind this key: an undeclared repo stops its
            # next work item when the worktree is built (Kraft-kji8w). That is
            # deliberate; being told here rather than by a parked item is what
            # makes it survivable.
            suggestion = config._first_setup_command(path) if path.is_dir() else None
            checks.append(
                _check(
                    f"setup {repo.get('name') or repo['path']}",
                    False,
                    f"no setup_command in repos.yaml — suggest: {suggestion or 'none found'}"
                    ' (use "" if this repo deliberately needs no preparation)',
                )
            )
        if auto:
            checks.append(_forge_check(repo))
    return checks or [_check("repos", True, "none connected")]


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
    release = update.latest()
    if release is None:
        # Not a skip: this ran and failed to get an answer, which on a
        # private project usually means no GITLAB_TOKEN and no authenticated
        # `glab` - worth a human's attention, unlike an opt-out or a missing
        # server.
        return _check("version", True, f"{here} (could not reach the release feed)", warn=True)
    if update.is_behind(release):
        return _check("version", True, f"{here} installed, {release.tag} available")
    return _check("version", True, f"{here} (the newest release)")
