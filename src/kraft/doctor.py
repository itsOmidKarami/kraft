"""`kraft admin doctor`: the checks a human runs when Kraft is misbehaving.

One pass, one line per check, no `--fix` — doctor reports and the human decides.

Every check here answers a question someone actually asked once. That bar is the
only thing that stops a doctor command from growing output nobody reads
(spec E §4), so a new check belongs here after it has been wanted, not before.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from kraft import auth, capabilities, client, config, detect, harness, pidfile, registration, render
from kraft import policy as policy_mod
from kraft.adapters import forge
from kraft.api import config_check
from kraft.executor import fallback
from kraft.paths import (
    BUNDLED,
    RunDirs,
    config_dir,
    default_config_dir,
    default_run_dir,
    default_skills_dir,
    linked_advice,
    linked_pre_2_config_dir,
)
from kraft.plugins import load as plugins_load
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import CHAINS_DIR, TemplateLibrary, TemplateLibraryError
from kraft.templates.models import AgentTask, ForgeTask
from kraft.worker import backends, channel, egress, kit
from kraft.worker import steering as steering_mod
from kraft.worker.backends import docker_forward
from kraft.worker.env import worker_env

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
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    if not run_dir.is_dir():
        # Said once, up front, so the FAILs below read as "not started yet"
        # rather than a broken install.
        checks.append(
            _check(
                "home",
                True,
                f"{run_dir} does not exist: no server has run on this home, so the "
                "rows it seeds fail until `kraft` has started once",
                warn=True,
            )
        )
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
    if health is not None:
        checks.append(_restart_check(health))
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


def _storage_line(payload: dict) -> tuple[str, str] | None:
    """`/health`'s `storage` as (state, one sentence), or None without a limit."""
    s = payload.get("storage")
    if not s:
        return None
    size = render.human_size
    used = f"worktrees use {size(s['used_bytes'])} of {size(s['limit_bytes'])}"
    free = "kraft view storage lists it by item; kraft item archive --reclaimable makes room"
    if s["state"] == "held":
        return "held", f"{used}: starts that need a new worktree are held; {free}"
    if s["state"] == "over_quota":
        return "over_quota", f"{used}, over the {size(s['quota_bytes'])} quota; {free}"
    return "ok", f"{used}; kraft view storage lists it by item"


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
    if payload.get("invalid_intake"):
        reasons.append(
            f"invalid intake.yaml, auto-intake and its schedules are off (a trigger left in "
            f"policy.yaml still fires): {payload['invalid_intake']}"
            if payload.get("intake_off")
            else f"invalid intake.yaml, not applied: the running auto-intake and schedules are "
            f"kept; fix the file and reload: {payload['invalid_intake']}"
        )
    reasons.extend(f"index: {error}" for error in index.get("errors") or [])
    orphaned = (payload.get("reattach_summary") or {}).get("unknown") or []
    if orphaned:
        reasons.append(f"orphaned agent sessions: {', '.join(orphaned)}")
    stored = _storage_line(payload)
    if stored and stored[0] == "held":
        reasons.append(f"storage: {stored[1]}")
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
    tail = (
        [extra, _check("storage", True, stored[1], warn=stored[0] == "over_quota")]
        if stored and stored[0] != "held"
        else [extra]
    )
    if not reasons:
        status = str(payload.get("status", "?"))
        return [_check("health", payload.get("status") == "ok", status), *tail]
    return [*(_check("health", False, reason) for reason in reasons), *tail]


def _restart_check(payload: dict) -> dict:
    """Is the server running the version installed on disk?

    `kraft admin update` without `--restart` replaces the package under a
    server that keeps running the old code, and the old server's interface is
    replaced with it. A warning, not a failure: nothing is broken, but nothing
    the board shows can be trusted until the restart. A server that does not
    report `installed` predates this check, so it is older than this install.
    """
    from kraft import update

    here = update.installed()
    running = payload.get("version")
    pid = payload.get("pid")
    who = f"the server (pid {pid})" if pid is not None else "the server"
    if "installed" not in payload:
        return _check(
            "restart",
            True,
            f"{who} runs a release older than the installed {here}: restart it to finish "
            "the update - kraft admin restart",
            warn=True,
        )
    if running != here:
        return _check(
            "restart",
            True,
            f"{who} runs {running}, but {here} is installed: restart it to finish the "
            "update - kraft admin restart",
            warn=True,
        )
    return _check("restart", True, f"{who} runs the installed version ({here})")


def _config_row(templates: Path) -> dict:
    """The directory in use, and a warning when a 1.x `templates/` still sits
    beside `config/` as a directory of its own (not the link the 2.0 rename
    leaves): one of the two is not read, and whatever it holds is invisible."""
    home = default_config_dir()
    old = home.with_name("templates")
    if old.is_dir() and not old.is_symlink() and home.is_dir() and any(old.iterdir()):
        return _check(
            "config",
            True,
            f"{templates}; {old} and {home} both exist and only one is read: "
            f"merge what {old} holds into {home} and remove it",
            warn=True,
        )
    if linked := linked_pre_2_config_dir():
        return _check("config", True, f"{templates}; {linked_advice(linked)}", warn=True)
    return _check("config", True, str(templates))


def _config_checks() -> list[dict]:
    templates = config_dir()
    if not templates.is_dir():
        # Every row still reports, as a skip: every check this function can emit
        # emits on every path, so a caller reading the run by name never has to
        # ask whether a row is missing because it passed, because it was skipped,
        # or because an earlier branch returned before reaching it.
        return [
            _check("config", False, f"{templates} does not exist — start `kraft` once to seed it"),
            _chain_templates_check(),
            _check("chains", True, "skipped: no config dir", skipped=True),
            # Nothing seeded yet, so nothing to upgrade: the first start seeds
            # every capability and stamps the version.
            _check("capabilities", True, "skipped: no config dir", skipped=True),
            _check("detectors.yaml", True, "skipped: no config dir", skipped=True),
            _check("notify.yaml", True, "skipped: no config dir", skipped=True),
            _check("plugins", True, "skipped: no config dir", skipped=True),
            _check("plugin updates", True, "skipped: no config dir", skipped=True),
            _token_check(),
            _token_check("trigger token", auth.TRIGGER_TOKEN_FILE),
        ]
    checks = [_config_row(templates)]
    try:
        access = config.Access.load(templates / "access.yaml")
        # Loaded as written, so this is the only place a bad one shows.
        bad = [why for h in access.allowed_hosts if (why := config.host_entry_problem(h))]
        bind = os.environ.get("KRAFT_HOST") or access.bind
        if bad and bind in config.LOOPBACK:
            # A loopback bind never reads the list, so a leftover entry
            # breaks nothing until the bind changes: a warning, not a failure
            # a `doctor && ...` script trips on after an upgrade.
            detail = "; ".join(bad) + f" (unused while bound to {bind})"
            checks.append(_check("access.yaml", True, detail, warn=True))
        elif bad:
            checks.append(_check("access.yaml", False, "; ".join(bad)))
        elif bind not in config.LOOPBACK and not access.allowed_hosts:
            # Every browser on another device is refused, and 1.4 let a
            # plain-http one through: the first an upgraded LAN user hears of it.
            checks.append(
                _check(
                    "access.yaml",
                    True,
                    f"bound to {bind} with no allowed_hosts: a browser on another "
                    "device is refused; add each name it uses on Settings › Access",
                    warn=True,
                )
            )
        else:
            checks.append(_check("access.yaml", True, "parses"))
    except config.ConfigError as exc:
        checks.append(_check("access.yaml", False, str(exc)))
    checks.append(_detectors_check(templates))
    checks.append(_notify_check(templates))
    checks.append(_moved_keys_check(templates))
    checks.append(_chain_templates_check())
    checks.append(_chains_check(templates))
    checks += _plugin_checks(templates)
    checks.append(_capabilities_check())
    checks.append(_token_check())
    checks.append(_token_check("trigger token", auth.TRIGGER_TOKEN_FILE))
    return checks


def _repo_entries(data: object) -> list:
    return [r for r in (data.get("repos") or []) if isinstance(r, dict)] if data else []


#: What 2.0 moved or renamed in the config files, each still read under its
#: old name: file, the old key, where it lives now, and whether the parsed
#: file still has it.
MOVED_KEYS: tuple[tuple[str, str, str, Callable[[dict], bool]], ...] = (
    (
        "intake.yaml",
        "max_concurrent",
        "policy.yaml's `max_concurrent`",
        lambda d: "max_concurrent" in d,
    ),
    (
        "policy.yaml",
        "triggers",
        "intake.yaml's `schedules`",
        lambda d: d.get("triggers") is not None,
    ),
    (
        "repos.yaml",
        "default_chain_template",
        "`default_chain` on the entry",
        lambda d: any("default_chain_template" in r for r in _repo_entries(d)),
    ),
    (
        "theme.yaml",
        "board.group_by: template",
        "`chain`",
        lambda d: isinstance(d.get("board"), dict) and d["board"].get("group_by") == "template",
    ),
)


def _why_it_stayed(key: str, data: dict) -> str:
    """Why the start did not move `policy.yaml`'s triggers (`carry_moved_keys`),
    which still fire but which Settings › Auto-intake does not show: a
    re-made copy there fires twice (R12c-02)."""
    triggers = data.get("triggers")
    if key != "triggers" or not isinstance(triggers, list):
        return ""
    refused = config.schedule_refusals(triggers, "triggers")
    if not refused:
        return "; the next start moves them"
    moving = len(triggers) - len(refused)
    out = f"; the next start moves {moving} of them" if moving else ""
    for index, why in refused.items():
        note = policy_mod.skipped_trigger(triggers[index])
        skipped = f" and is skipped, {note}" if note else ""
        out += (
            f"; triggers.{index} stays because intake.yaml's schedules refuse {why}{skipped}"
            ": fix it and restart"
        )
    return out


def _moved_keys_check(templates: Path) -> dict:
    """A key 2.0 moved, still written under its old name: read, so nothing
    stops, and named here so the file gets tidied rather than carrying a
    key no reader honours (intake.yaml's `max_concurrent`) or one the
    Settings screens no longer show (policy.yaml's `triggers`)."""
    found: list[str] = []
    for name, key, now, has in MOVED_KEYS:
        try:
            data = config.read_yaml(templates / name, {})
        except config.ConfigError:
            continue  # its own row says so
        if isinstance(data, dict) and has(data):
            found.append(f"{name}: {key} is {now} since 2.0" + _why_it_stayed(key, data))
    if not found:
        return _check("moved keys", True, "none: every key is where 2.0 reads it")
    return _check("moved keys", True, "; ".join(found), warn=True)


def _detectors_check(templates: Path) -> dict:
    """The operator's `detectors.yaml`: optional, and read only when a repo is
    connected, so a broken one would otherwise surface as a failed connect
    long after it was written."""
    own = templates / detect.FILE
    if not own.is_file():
        return _check("detectors.yaml", True, "not present: the packaged detectors alone")
    try:
        detect.load(templates)
        count = len(detect.DetectorFile.model_validate(config.read_yaml(own, {})).detectors)
    except config.ConfigError as exc:
        return _check("detectors.yaml", False, str(exc))
    return _check("detectors.yaml", True, f"parses ({count} detector(s) of your own)")


def _notify_check(templates: Path) -> dict:
    """`notify.yaml`: one that does not load turns every notification off, and
    the only other record of that is a warning in the server log.

    This reads the file, not the server: the notifier rereads it only at start
    and on a save in Settings, so the rows say what the file holds and a
    failure names the restart that applies the fix."""
    try:
        cfg = config.Notify.load(templates / config.Notify.FILE).model_dump()
    except config.ConfigError as exc:
        return _check(
            "notify.yaml",
            False,
            f"{exc}; no notification is sent - fix it, then kraft admin restart",
        )
    if why := config_check.notify_problem(cfg):
        return _check("notify.yaml", False, f"notify.yaml: {why}; then kraft admin restart")
    if not cfg["enabled"]:
        return _check("notify.yaml", True, "parses (enabled: false)")
    return _check("notify.yaml", True, f"parses (enabled, {len(cfg['events'])} event type(s))")


def _pidfile_check() -> dict:
    """A pidfile naming a process that is gone looks, to every other check
    here, exactly like no server ever started -- nothing before this ever
    said so (Kraft-mqwg). One naming a live process that is not this run
    dir's server (a reused pid, another user's process) is worse: `kraft
    admin stop` used to signal it. `kraft.pidfile` decides which it is."""
    pid_path = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).pid
    state = pidfile.read(pid_path)
    if state.running:
        return _check("pidfile", True, f"{pid_path} (pid {state.pid})")
    if state.pid is None and state.why == "no pidfile":
        return _check("pidfile", True, "no pidfile")
    if state.pid is None:
        return _check("pidfile", False, f"{pid_path}: {state.why}")
    return _check(
        "pidfile",
        False,
        f"{pid_path} names pid {state.pid}, which {state.why} - stale; "
        "the next `kraft admin start` or `stop` clears it",
    )


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
        except (OSError, ValueError, yaml.YAMLError):
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
    shipped_dir = BUNDLED / "config"
    if not (shipped_dir / CHAINS_DIR).is_dir():
        return _check("chain_templates", True, "skipped: not an installed Kraft", skipped=True)
    shipped = _chain_template_files(shipped_dir)
    if not shipped:
        return _check(
            "chain_templates", True, "skipped: no chain templates in this version", skipped=True
        )
    live_dir = config_dir()
    if not live_dir.is_dir():
        # A home never started: nothing is missing from a copy not made yet.
        return _check("chain_templates", True, "skipped: no config dir", skipped=True)
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
        "Templates › Chains, or edit those files",
    )


def _chains_check(templates: Path) -> dict:
    """Every chain of the live library that does not resolve, by id and with
    the resolver's own message (Kraft-n1zp9) -- `admin templates lint`'s pass,
    read from disk so it answers with no server running. A chain that does
    not resolve otherwise only shows up as the next intake on it failing."""
    skills = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    report = TemplateLibrary.lint_dir(
        templates, skills_dir=skills, plugins=plugins_load.installed(templates)
    )
    if report.valid:
        return _check("chains", True, f"{len(report.chains)} chain(s) resolve")
    return _check("chains", False, "; ".join(str(issue) for issue in report.issues))


def _plugin_checks(templates: Path) -> list[dict]:
    """The installed plugins as the files say, with no server running: what
    does not load and why, what waits for a person, and each auto-updating
    plugin's last outcome."""
    from kraft.plugins import fetch
    from kraft.plugins import update as plugin_update
    from kraft.plugins.config import PluginsConfig, PluginsLock

    if why := plugins_load.config_problem(templates):
        return [
            _check("plugins", False, why),
            _check("plugin updates", True, "skipped: the plugin files do not read", skipped=True),
        ]
    root = plugins_load.plugins_dir()
    listed = PluginsConfig.load(templates / PluginsConfig.FILE)
    lock = PluginsLock.load(templates / PluginsLock.FILE)
    found = plugins_load.installed(templates, root, verify=True)
    broken = [f"{p.id}: {p.left_out}" for p in found if p.left_out is not None and not p.quiet]
    notes = [f"{p.id} {p.left_out}" for p in found if p.quiet and p.left_out != "is disabled"]
    auto = []
    for plugin_id, locked in lock.plugins.items():
        if plugin_id not in listed.plugins:
            continue
        name = plugin_id.split("@", 1)[1]
        collection = listed.collections[name]
        if listed.namespace(plugin_id) != locked.namespace:
            notes.append(f"{plugin_id}: alias change pending; run kraft admin plugin update")
        if collection.ref != locked.ref:
            notes.append(f"{plugin_id}: ref change pending; run kraft admin plugin update")
        if plugin_update._moved(collection, locked):
            notes.append(
                f"{plugin_id}: collection URL change pending; run kraft admin plugin update"
            )
        if collection.auto_update or listed.entry(plugin_id).auto_update:
            auto.append(plugin_id)
        if collection.git is not None and locked.commit is not None:
            mirror = fetch.mirror_path(root, name, collection.git)
            try:
                fetch._git("cat-file", "-e", f"{locked.commit}^{{commit}}", git_dir=mirror)
            except (fetch.FetchError, OSError):
                notes.append(
                    f"{plugin_id}: locked commit {locked.commit[:12]} is not in the local "
                    "mirror; a restore would have to fetch it"
                )
    notes += [
        f"collection {name} is a local directory: not reproducible on other machines "
        "(publish it as a git collection to share it)"
        for name, collection in listed.collections.items()
        if collection.path is not None
    ]
    if broken:
        plugins = _check("plugins", False, "; ".join(broken + notes))
    elif notes:
        plugins = _check("plugins", True, "; ".join(notes), warn=True)
    else:
        loaded = sum(1 for p in found if p.left_out is None)
        plugins = _check("plugins", True, f"{loaded} plugin(s) load")

    status = plugin_update.read_status(root)
    waiting = [
        f"{plugin_id}: {entry.get('outcome')}"
        + (f" ({entry['kind']})" if entry.get("kind") else "")
        + (f": {entry['message']}" if entry.get("message") else "")
        + (
            "; a server run by launchd or systemd may lack the SSH agent or credential "
            "helper your shell has"
            if entry.get("kind") == "auth"
            else ""
        )
        for plugin_id, entry in status.items()
        if plugin_id in auto and entry.get("outcome") in ("held", "failed")
    ]
    try:
        policy = config.read_yaml(templates / "policy.yaml")
    except config.ConfigError:
        policy = {}
    defaults = policy.get("defaults") if isinstance(policy.get("defaults"), dict) else {}
    if auto and not (
        policy.get("maxima") or defaults.get("allowed_tools") or defaults.get("sandbox")
    ):
        waiting.append(
            f"{', '.join(auto)} auto-update(s) on an instance with no allowed_tools, sandbox "
            "or maxima: in policy.yaml: an update can change what a run does unattended"
        )
    if waiting:
        updates = _check("plugin updates", True, "; ".join(waiting), warn=True)
    else:
        updates = _check("plugin updates", True, f"{len(auto)} plugin(s) auto-update")
    return [plugins, updates]


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
    live_dir = config_dir()
    stamp_path = live_dir / ".seeded-version"
    try:
        stamp = stamp_path.read_text().strip() or None
    except OSError:
        # Seeded before stamping existed, or unreadable. Either way the home
        # knows nothing about itself; `added_since(None)` says everything.
        stamp = None
    missing = [c for c in capabilities.added_since(stamp) if not capabilities.adopted(c, live_dir)]
    seeded = f"seeded at {stamp}" if stamp else "seeded before versions were recorded"
    if not missing:
        return _check("capabilities", True, f"{seeded}; up to date")
    lines = [f"{seeded}; {len(missing)} capability(ies) added since"]
    for c in missing:
        lines.append(f"  {c.version}  {c.name}: {c.what}")
        lines.append(f"      -> {c.how}")
    return _check("capabilities", True, "\n".join(lines))


def _token_check(name: str = "mcp token", file: str = auth.MCP_TOKEN_FILE) -> dict:
    """The MCP bearer is a local credential: every other user on the machine can
    drive Kraft with a copy of it, so its mode is part of whether Kraft is well.
    The trigger token only files work, and gets the same check."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    path = run_dir / file
    if not auth.read_mcp_token(run_dir, file):
        return _check(name, False, f"missing or empty at {path} — the server writes it on start")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        return _check(name, False, f"{path}: {exc}")
    if mode & 0o077:
        return _check(name, False, f"{path} is {mode:04o}, not 0600 — any local user can read it")
    return _check(name, True, f"{path} ({mode:04o})")


def _agent_checks() -> list[dict]:
    """One PATH check per harness profile the live library's chains select,
    plus one failure row per harness file or profile table that failed to load.

    Read from disk, the same `KRAFT_CONFIG_DIR` precedence dispatch reads
    `harnesses.yaml` by (`agent.harness_profile`). A chain that does not resolve
    selects nothing here: `/health` already names the library error, and a
    second copy of it helps nobody. A shipped profile nothing selects is not a
    missing dependency and gets no row.
    """
    live = config_dir()
    harnesses = harness.load(None)
    checks: list[dict] = []
    try:
        table = HarnessProfileTable.from_yaml(
            live / "harnesses.yaml",
            harnesses=harnesses.valid,
            plugins=plugins_load.installed(live),
        )
    except TemplateEnvironmentError as exc:
        detail = (
            f"{live} does not exist — start `kraft` once to seed it"
            if not live.is_dir()
            else str(exc)
        )
        checks.append(_check("harnesses.yaml", False, detail))
        table = HarnessProfileTable(profiles={})
    profiles = table.profiles
    chains = _resolved_chains(live)
    selected = _selected_profiles(chains)
    for pid in sorted(selected):
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
                _check(
                    f"agent: {pid}",
                    False,
                    f"`{exe}` is not on PATH — {_cannot_run(selected[pid], len(chains))}",
                )
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
    return checks + _pairing_checks(live, table, harnesses) + _cost_checks(live, harnesses)


def _cost_checks(live: Path, harnesses) -> list[dict]:
    """One warning per harness whose output carries no cost (codex, cursor,
    amp) and that the chains launch on a model `prices.json` does not price,
    or on no model at all (Kraft-9efnk.10). Its spend then has no dollar
    figure: the item and daily dollar caps count it as $0, and a per-scope
    `budget_usd` stops on it. Resolved per launch, fallbacks included, the way
    `resolve_agent_task` does it; a repo's `models:` or an item's override is
    not seen here. A launch that cannot resolve has its own row above."""
    from kraft import usage
    from kraft.adapters import agent

    unpriced: dict[str, set[str]] = {}
    table = None
    for chain in _resolved_chains(live):
        for node in chain.nodes:
            for t in node.tasks():
                if not isinstance(t.task, AgentTask):
                    continue
                try:
                    table = table or agent.harness_table(harnesses)[0]
                    candidates = fallback.candidates(t.task, table)
                except agent.HarnessUnavailable:
                    return []
                for c in candidates:
                    try:
                        inv = agent.resolve_agent_task(
                            c, None, None, harnesses=harnesses, steering=chain.steering
                        )
                    except (agent.HarnessUnavailable, steering_mod.SteeringError):
                        continue
                    reader = harnesses.valid[inv.harness].capabilities["usage"].reader
                    if reader in usage.READERS and not usage.READERS[reader].reports_cost:
                        if not usage.priced(inv.model):
                            # Named by its harnesses.yaml profile, as a person set it up.
                            unpriced.setdefault(c.harness, set()).add(inv.model or "(its default)")
    return [
        _check(
            f"cost: {h}",
            True,
            f"reports no cost, and prices.json has no rate for {', '.join(sorted(models))}: "
            "the item and daily dollar caps count its spend as $0, and a budget_usd stops "
            "on it. Bound it with a token_budget.",
            warn=True,
        )
        for h, models in sorted(unpriced.items())
    ]


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
        library = TemplateLibrary.from_yaml_dir(live, plugins=plugins_load.installed(live))
    except TemplateLibraryError:
        return []
    chains = []
    for id in library.chain_ids:
        try:
            chains.append(library.resolve_chain(id))
        except TemplateLibraryError:
            continue
    return chains


def _selected_profiles(chains: list) -> dict[str, list[str]]:
    """Each harness profile `chains` select, and the ids of the chains that do."""
    selected: dict[str, list[str]] = {}
    for chain in chains:
        for pid in {
            t.task.harness
            for node in chain.nodes
            for t in node.tasks()
            if isinstance(t.task, AgentTask)
        }:
            selected.setdefault(pid, []).append(chain.id)
    return selected


#: How many chain ids a doctor row names before it counts the rest.
_CHAINS_NAMED = 5


def _cannot_run(chain_ids: list[str], of: int) -> str:
    """What a missing agent stops: every chain only when it is in all of them."""
    if len(chain_ids) == of:
        return "no chain can run"
    if len(chain_ids) == 1:
        return f"chain {chain_ids[0]} can't run"
    named = sorted(chain_ids)
    more = f" and {len(named) - _CHAINS_NAMED} more" if len(named) > _CHAINS_NAMED else ""
    return f"chains {', '.join(named[:_CHAINS_NAMED])}{more} can't run"


#: What the `mcp server` row does not read (Kraft-9efnk.43): each other
#: agent keeps its registration in its own files and scopes, and doctor never
#: launches an agent CLI to ask it.
_MCP_SCOPE = (
    "Claude Code's registration only; Codex, Cursor, OpenCode, Amp, Antigravity "
    "and Gemini CLI are not checked, see each one's own MCP list command"
)


async def _mcp_check(server_up: bool) -> dict:
    """Is the Kraft MCP server registered with Claude Code?

    Next to `_agent_checks` because it answers the same question: can this
    machine actually launch a worker. Every unsandboxed Claude launch names
    Kraft's permission tool, by the name `registration.permission_tool`
    resolves -- direct (`kraft admin init`, a committed `.mcp.json`) or the
    Kraft plugin's -- and a launch with none registered is refused by name. A
    plugin-only install passes: no `kraft admin init` needed.

    `ok=False`, unlike `bd` or shell completion: those are choices an operator
    made, this one stops every Claude worker.

    Rejected, as the bead records: passing `--mcp-config` on every launch, which
    would make Kraft write the operator's agent config.
    """
    found = registration.permission_tool(None)
    asking = sorted(_direct_askers(harness.load(None)))
    needed = True
    if server_up:
        # Each connected repo on its own: its committed settings can switch
        # the plugin off for its workers, and its local scope or `.mcp.json`
        # can register the server where the user scope does not.
        entries = [
            config.RepoEntry.model_validate(r, context={"unrecognised_keys_reported": True})
            for r in await client.repos()
        ]
        per_repo = {Path(e.path): registration.permission_tool(Path(e.path)) for e in entries}
        refused = [str(repo) for repo, tool in per_repo.items() if tool is None]
        needed = _launches_direct_asker(entries)
        # Every repo refused and nothing at user scope is no registration at
        # all: it falls through to the same failure as no repo connected,
        # since the first such worker anywhere is refused -- when the chains
        # launch one at all.
        if refused and (found or len(refused) < len(per_repo) or not needed):
            # A warning, not a failure (Kraft-9efnk.31): the launch refuses
            # only an unsandboxed task on a harness asking through the direct
            # tool, and whether a repo's items ever launch one depends on
            # chain, sandbox and item overrides doctor does not resolve.
            return _check(
                "mcp server",
                True,
                f"no kraft MCP server registered for Claude workers in {', '.join(refused)}: "
                f"an unsandboxed task there on harness {' or '.join(asking)} is refused; "
                f"{registration.FIX} ({_MCP_SCOPE})",
                warn=True,
            )
        found = found or next(iter(per_repo.values()), None)
    elif found is None:
        # The repo-scope half needs `GET /repos`, like every other
        # server-dependent check here.
        return _check("mcp server", True, "skipped: no server", skipped=True)
    if found:
        return _check("mcp server", True, f"{found[0]}, registered in {found[1]} ({_MCP_SCOPE})")
    if not needed:
        return _check(
            "mcp server",
            True,
            f"no kraft MCP server registered: no chain launches an unsandboxed task on harness "
            f"{' or '.join(asking)} here, which is the launch that needs it; {registration.FIX} "
            f"before one does ({_MCP_SCOPE})",
            warn=True,
        )
    return _check(
        "mcp server",
        False,
        f"no kraft MCP server registered, so Claude workers are refused; {registration.FIX} "
        f"({_MCP_SCOPE})",
    )


def _direct_askers(harnesses) -> set[str]:
    """The providers whose launch asks Kraft's permission gate through the
    host's registered `kraft` MCP server (`approval_channel.always`)."""
    return {
        hid
        for hid, h in harnesses.valid.items()
        if (c := h.capabilities.get("approval_channel")) is not None
        and c.always == registration.DIRECT
    }


def _launches_direct_asker(entries: list[config.RepoEntry]) -> bool:
    """Can a worker here need the registered server? The launch refuses only
    `not sandbox and asks.always == DIRECT` (`adapters.agent`): so no when
    every connected repo is sandboxed, or when no task of the live library,
    fallbacks included, runs on a profile whose provider asks that way (a
    Codex-only setup). A harness table that does not load answers yes: its
    own row fails, and this one must not pass on a guess."""
    if entries and all(e.effective_sandbox is not None for e in entries):
        return False
    live = config_dir()
    harnesses = harness.load(None)
    direct = _direct_askers(harnesses)
    try:
        table = HarnessProfileTable.from_yaml(
            live / "harnesses.yaml",
            harnesses=harnesses.valid,
            plugins=plugins_load.installed(live),
        )
    except TemplateEnvironmentError:
        return True
    for chain in _resolved_chains(live):
        for node in chain.nodes:
            for t in node.tasks():
                if not isinstance(t.task, AgentTask):
                    continue
                entries_, _source = fallback.fallback_list(t.task, table)
                for task in [t.task, *(fallback.apply(t.task, e) for e in entries_)]:
                    profile = table.profiles.get(task.harness)
                    if profile is None or profile.provider in direct:
                        return True
    return False


def _path_check() -> dict:
    """Is the `kraft` on PATH this one? Every MCP registration runs it by name,
    so an older install ahead of this one on PATH answers every agent's tool
    call with that install's code, whatever `kraft admin update` installed.
    No `kraft` on PATH at all fails too: those same registrations then start
    nothing, the usual fresh `uv tool install` whose bin dir is not on PATH yet.
    `ok=False` for the same reason as `_mcp_check`: it breaks silently."""
    from kraft import update

    found = shutil.which("kraft")
    if found is None:
        return _check(
            "kraft on PATH",
            False,
            "not on PATH -- MCP servers and hooks run `kraft` by name; add its directory to "
            "PATH (`uv tool update-shell` for a uv install), then restart Kraft from a new "
            "shell. Under a service, reinstall it from that shell (`kraft admin "
            "uninstall-service`, then `kraft admin install-service`): the unit keeps the PATH "
            "it was installed with",
        )
    other = update.shadowing_kraft()
    if other is None:
        return _check("kraft on PATH", True, found)
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
    live = config_dir()
    return any(
        isinstance(t.task, ForgeTask)
        for chain in _resolved_chains(live)
        for node in chain.nodes
        for t in node.tasks()
    )


async def _sandbox_check(repo: config.RepoEntry, policy, asked: set[str]) -> dict:
    """Can this repository's sandbox run here at all? Its work items stop
    for a human otherwise, and better learned here. Each backend asks the
    machine once per run (`asked`, its kinds so far): a runtime that does
    not answer costs its wait once, not once per sandboxed repository."""
    value = policy.model_dump()
    backend = backends.for_sandbox(value)
    ok, detail = await backend.health(value, refresh=backend.kind not in asked)
    asked.add(backend.kind)
    return _check(f"sandbox {_label(repo)}", ok, detail)


async def _kit_checks(repo: config.RepoEntry, policy) -> tuple[object, list[dict]]:
    """A `kind: kit` sandbox fetched and lowered, as the walk does before an
    item starts, and the lowered policy for the rows after it; or None and
    a failed sandbox row naming the Kit and why. Warns of each harness host
    the Kit leaves out (its sessions are refused them, spec §9.4) and each
    optional credential with no binding (skipped)."""
    name = _label(repo)
    try:
        fetched, lowered = await kit.resolve(policy.kit)
        bound = kit.bindings()
    except (kit.KitRefused, config.ConfigError, OSError) as exc:
        return None, [_check(f"sandbox {name}", False, f"the Kit {policy.kit} is refused: {exc}")]
    checks = []
    runtime = lowered.policy.network.runtime
    allow, deny = (runtime.allow, runtime.deny) if runtime else ((), ())
    left_out = {
        i: missing
        for i, h in harness.load(None).valid.items()
        if (
            missing := [
                host
                for host in h.network_requires
                if not egress.match(host.split(":")[0], 443, allow, deny).allowed
            ]
        )
    }
    if left_out:
        said = "; ".join(f"{i} requires {', '.join(hosts)}" for i, hosts in left_out.items())
        checks.append(
            _check(
                f"kit hosts {name}",
                True,
                f"the Kit does not allow what these harnesses need, so their sessions "
                f"are refused it: {said}",
                warn=True,
            )
        )
    unbound = sorted(
        c.config.service
        for c in kit.claims(fetched.descriptor()).credentials
        if c.config.service not in bound
    )
    if unbound:
        checks.append(
            _check(
                f"kit credentials {name}",
                True,
                f"no binding for {', '.join(unbound)}, so it is skipped: add "
                "`<service>: <DAEMON_ENV_NAME>` under sandbox.yaml's credentials",
                warn=True,
            )
        )
    return lowered.policy, checks


def _egress_check(repo: config.RepoEntry, policy) -> dict | None:
    """A sandbox with no `network:` has open egress, the cloud metadata
    address included, and every agent launch into it is refused unless the sandbox
    says `unrestricted_network: true` (`adapters.agent`): a failure here, a
    warning once it does."""
    if policy.network is not None:
        return None
    if policy.unrestricted_network:
        return _check(
            f"egress {_label(repo)}",
            True,
            "`unrestricted_network: true`: sandboxed tasks here have open egress and "
            "gates are not enforced; set `network:` in the sandbox policy to allow "
            "only the hosts they need",
            warn=True,
        )
    return _check(
        f"egress {_label(repo)}",
        False,
        "the sandbox has no `network:`, so every agent launch into it is refused: add a "
        "`network:` policy, or set `unrestricted_network: true` on the sandbox to run "
        "with open egress and gates not enforced",
    )


def _proxy_check(repo: config.RepoEntry, policy) -> dict | None:
    """A warning when this machine's proxy is on its loopback and the sandbox
    sets no `network:`: a container has a loopback of its own, so the proxy
    is not forwarded and a sandboxed task goes direct, which fails wherever
    only the proxy gets out. Under `network:` Kraft's own proxy chains to it
    from the host. Read from doctor's own environment, which is the daemon's
    when both were started from the same shell."""
    if policy.network is not None:
        return None
    loopback = docker_forward.loopback_proxies()
    if not loopback:
        return None
    named = ", ".join(f"{name}={value}" for name, value in loopback.items())
    return _check(
        f"proxy {_label(repo)}",
        True,
        f"{named} is on this machine's loopback, which a container cannot reach, so "
        "sandboxed tasks run without it; `network:` in the sandbox policy routes them "
        "through Kraft's own proxy instead",
        warn=True,
    )


def _ignored_ca_check(repo: config.RepoEntry) -> dict | None:
    """A warning when this machine's `SSL_CERT_FILE` is set but cannot serve
    as a sandbox's extra CA, so it is ignored and sandboxed tasks trust only
    their image's roots. Read from doctor's own environment, like
    `_proxy_check`. A `sandbox.yaml` that does not parse is the sandbox row's
    to report."""
    try:
        why = docker_forward.ignored_ssl_cert_file()
    except config.ConfigError:
        return None
    if why is None:
        return None
    return _check(
        f"ca {_label(repo)}",
        True,
        f"SSL_CERT_FILE is ignored for sandboxed tasks: {why}; they trust only their "
        "image's roots. Fix it, or name a CA in sandbox.yaml `ca_bundle`",
        warn=True,
    )


def _credential_check(repo: config.RepoEntry, policy) -> dict | None:
    """Which names this sandbox's egress proxy holds, on which
    hosts (as each harness resolves them), and which
    names a harness declares still pass through. A managed name with no value
    fails: the proxy refuses every request that should carry it. The value is
    looked for as a launch reads it, `worker_env` of this entry (or, for one
    bound to a `source`, that name alone), in doctor's own environment, which
    is the daemon's when both were started from the same shell."""
    if not policy.credentials:
        return None
    harnesses = harness.load(None).valid.values()
    hosts: dict[str, set[str]] = {c.env: set() for c in policy.credentials}
    for h in harnesses:
        for cred in h.managed_credentials(policy.credentials):
            hosts[cred.env].update(rule.domain for rule in cred.inject)
    passing = sorted({c.env for h in harnesses for c in h.credentials} - hosts.keys())
    environ = worker_env(repo)
    # A bound credential's value is the daemon's under its `source` alone.
    missing = list(
        dict.fromkeys(
            c.source or c.env
            for c in policy.credentials
            # Set but empty is missing too: the launch refuses it the same.
            if (not os.environ.get(c.source) if c.source else c.env not in environ)
        )
    )
    phases: dict[str, set[str]] = {c.env: set() for c in policy.credentials}
    for c in policy.credentials:
        phases[c.env].update(c.phases())
    only = {name: f" ({p} only)" for name, ps in phases.items() if len(ps) == 1 for p in ps}
    detail = "proxy-managed: " + "; ".join(
        f"{name}{only.get(name, '')} on {', '.join(sorted(on)) or 'no host (sentinel only)'}"
        for name, on in hosts.items()
    )
    if passing:
        detail += f"; passes through: {', '.join(passing)}"
    # A name alone that no harness declares: nothing injects it, silently.
    declared = {i: [c.env for c in h.credentials] for i, h in harness.load(None).valid.items()}
    orphans = [
        c.env
        for c in policy.credentials
        if not c.inject and not any(c.env in names for names in declared.values())
    ]
    if orphans:
        by = "; ".join(f"{i} declares {', '.join(names)}" for i, names in declared.items() if names)
        detail = (
            f"{', '.join(orphans)} is managed but no harness declares how to inject it, "
            f"so it is only a sentinel; {by}; {detail}"
        )
    if missing:
        detail = f"no value for {', '.join(missing)} here, so its requests are refused; {detail}"
    return _check(f"credentials {_label(repo)}", not missing, detail, warn=bool(orphans))


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


async def _tls_listener_check() -> dict:
    """The server's egress TLS listener, which a sandbox's relay dials on a
    runtime in a VM, answering as Kraft's own on the port it persisted."""
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir()))
    problem = await asyncio.to_thread(channel.tls_listener_problem, run_dirs)
    detail = problem or f"answers on 127.0.0.1:{channel.tls_port(run_dirs)}"
    return _check("egress listener", problem is None, detail)


def _probed(path: Path, templates_dir: Path) -> detect.Proposal | None:
    """What `kraft repo connect` would propose today, or None when it cannot
    read the repo or `detectors.yaml`: the row already fails, and a second
    error would only bury it."""
    if not path.is_dir():
        return None
    try:
        return detect.probe(path, templates_dir)
    except config.ConfigError:
        return None


def _tests_check(repo: config.RepoEntry, probed: detect.Proposal | None) -> dict | None:
    """A repo whose items run no test: `test_command: ""` passes verify with
    none, and an entry with no test command stops each item there. An entry
    connected before this release's probe can be the second where the probe
    now finds a command, and `kraft repo connect` again saves it."""
    if repo.test_scopes:
        return None
    name = f"tests {_label(repo)}"
    if repo.test_command == "":
        return _check(
            name,
            True,
            'test_command "" — work items here pass verify without running a test',
            warn=True,
        )
    if repo.test_command is not None:
        return None
    found = probed.test_command if probed else None
    advice = (
        f"connect proposes `{found}` now: run `kraft repo connect {repo.path}` again to save it"
        if found
        else 'set one under Settings › Repos, or `""` if it has no tests'
    )
    return _check(
        name, True, f"no test command, so its work items stop at verify; {advice}", warn=True
    )


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
    live = config_dir()
    profiles = _library_steering(live)
    # Retyped from the wire: the checks below read the entry the loader
    # models, not a dict whose keys each one must spell right. The loader's
    # own unrecognised-key warning stays quiet: doctor fails a row on them.
    repos = [
        config.RepoEntry.model_validate(r, context={"unrecognised_keys_reported": True})
        for r in await client.repos()
    ]
    networked = False
    asked: set[str] = set()
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
        # Probed only for what the entry leaves undecided, as connecting it
        # again would: a decided entry is the operator's, and costs no probe.
        undecided = repo.setup_command is None or (
            repo.test_command is None and not repo.test_scopes
        )
        probed = await asyncio.to_thread(_probed, path, live) if undecided else None
        if repo.setup_command is None:
            # No default stands behind this key: an undeclared repo stops its
            # next work item when the worktree is built (Kraft-kji8w). That is
            # deliberate; being told here rather than by a parked item is what
            # makes it survivable.
            suggestion = probed.setup_command if probed else None
            again = f"; run `kraft repo connect {repo.path}` again to save it" if suggestion else ""
            checks.append(
                _check(
                    f"setup {_label(repo)}",
                    False,
                    f"no setup_command in repos.yaml — suggest: {suggestion or 'none found'}"
                    f' (use "" if this repo deliberately needs no preparation){again}, or tick '
                    "No setup needed under Settings › Repos",
                )
            )
        if (tests := _tests_check(repo, probed)) is not None:
            checks.append(tests)
        unrecognised = config.unrecognised_repo_keys(repo.model_extra or {})
        if unrecognised:
            # Loaded, with a warning nobody may be reading: a key that binds
            # nothing is exactly what a typo looks like (Kraft-4hn34). A key an
            # older Kraft wrote is not a typo: a warning naming what replaced
            # it, not a failure an upgraded `doctor && ...` trips on.
            retired = [k for k in unrecognised if k in config.RETIRED_REPO_KEYS]
            unknown = [k for k in unrecognised if k not in config.RETIRED_REPO_KEYS]
            detail = "; ".join(
                [f"repos.yaml keys nothing reads: {', '.join(unknown)} -- remove them"]
                * bool(unknown)
                + [f"{k} is no longer read: {config.RETIRED_REPO_KEYS[k]}" for k in retired]
            )
            checks.append(_check(f"keys {_label(repo)}", not unknown, detail, warn=not unknown))
        if (policy := repo.effective_sandbox) is not None and policy.kind == "kit":
            # The rows below read the docker policy it lowers to.
            policy, found = await _kit_checks(repo, policy)
            checks.extend(found)
        if policy is not None:
            checks.append(await _sandbox_check(repo, policy, asked))
            networked = networked or policy.network is not None
            for extra in (
                _egress_check(repo, policy),
                _proxy_check(repo, policy),
                _ignored_ca_check(repo),
                _credential_check(repo, policy),
            ):
                if extra is not None:
                    checks.append(extra)
        if auto:
            checks.append(_forge_check(repo))
        if repo.steering and profiles is not None:
            checks.append(_steering_check(repo, profiles))
    checks.extend(_duplicate_repo_checks(repos))
    if networked:
        checks.append(await _tls_listener_check())
    return checks or [_check("repos", True, "none connected")]


def _duplicate_repo_checks(repos: list[config.RepoEntry]) -> list[dict]:
    """One warning per pair of entries that are one repository, one a person
    connected and the other a detected submodule checkout -- a member
    connected on its own and its root's stub of it, from before connecting
    learned to tell (Kraft-d7aj3). Two stubs under two roots, or two clones
    a person connected, are deliberate. A workspace naming the
    auto-connected one runs its member with none of the other's settings."""
    ids = [
        (repo, config.repository_identity(repo.path)) for repo in repos if Path(repo.path).is_dir()
    ]
    return [
        _check(
            f"duplicate {_label(a)}",
            True,
            f"{a.path} and {b.path} are one repository; keep the one you configured, "
            "point any workspace member at its id, and disconnect the other",
            warn=True,
        )
        for i, (a, ida) in enumerate(ids)
        for b, idb in ids[i + 1 :]
        if a.managed != b.managed
        and (config.same_repository(ida, idb) or config.same_repository(idb, ida))
    ]


def _library_steering(live: Path) -> dict[str, str] | None:
    """The live library's steering profiles, name to instructions, the shape
    `steering.select` reads; `None` when the library itself does not load --
    `_chains_check` already reports that failure, and a second copy of it per
    repo helps nobody."""
    try:
        library = TemplateLibrary.from_yaml_dir(live, plugins=plugins_load.installed(live))
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
    of them is safe to delete is a judgement about uncommitted work.

    A cancelled item keeps its worktree until it is archived, so every row
    counts, ended and archived ones included, not just the board's."""
    worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
    if not worktrees.is_dir():
        return _check("worktrees", True, f"{worktrees} does not exist yet")
    known = await client.work_item_ids()
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
