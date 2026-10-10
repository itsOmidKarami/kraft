"""This machine's server and its install: seed a home if there isn't one, then
serve; `start` is what bare `kraft` runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import httpx
import uvicorn
import yaml

from kraft import client, config, permission_hooks, pidfile, render
from kraft.cli import common, plugin, templates
from kraft.paths import (
    BUNDLED,
    LEGACY_CONFIG_DIR_VAR,
    RunDirs,
    config_dir,
    default_config_dir,
    default_run_dir,
    linked_advice,
    linked_pre_2_config_dir,
    names_default_config_dir,
    pre_2_config_dir,
)
from kraft.policy import CarriedPolicy
from kraft.vocab import WorkItemStatus

#: launchd label / systemd unit name. One daemon, one name -- not
#: per-instance, since the spec is about supervising *the* daemon.
_LAUNCHD_LABEL = "com.kraft.daemon"
_SYSTEMD_UNIT = "kraft.service"

#: Env vars that define which instance this is. Carried into the unit so the
#: service supervises the same instance the human's shell was pointed at, not
#: a fresh, empty ~/.kraft (spec §1: "the unit must carry the same
#: environment the human's shell had -- KRAFT_HOME above all, or the service
#: silently supervises a *different* instance").
_INSTANCE_ENV_VARS = (
    "KRAFT_HOME",
    "KRAFT_RUN_DIR",
    "KRAFT_CONFIG_DIR",
    # Its 1.x name, carried so a shell still pointing with it supervises the same instance.
    "KRAFT_TEMPLATES_DIR",
    "KRAFT_SKILLS_DIR",
    "KRAFT_HOST",
    "KRAFT_PORT",
)


def _instance_env() -> dict[str, str]:
    """The instance vars, plus PATH.

    Every agent the daemon spawns is a bare `claude`/`bd` argv resolved
    through PATH (adapters/subprocess.py's {**os.environ, ...}), and
    launchd/systemd hand a supervised process their own minimal PATH, not
    the shell's. Without this, the service starts, health reports ok, and
    every agent launch fails. `_kraft_executable()` covers `kraft` itself;
    this covers everything `kraft` then spawns.
    """
    env = {name: os.environ[name] for name in _INSTANCE_ENV_VARS if name in os.environ}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    return env


def _kraft_executable() -> str:
    """The same binary a human would run by hand -- resolved once and
    written into the unit, so it points at a real path rather than relying
    on the service manager's own (usually minimal) PATH."""
    found = shutil.which("kraft")
    if not found:
        raise SystemExit(
            "kraft admin install-service: `kraft` is not on PATH - install it "
            "the normal way first (`just install`, or install.sh)."
        )
    return found


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_unit_path() -> Path:
    # `systemctl --user` resolves unit files under $XDG_CONFIG_HOME when set,
    # falling back to ~/.config only when it is not -- writing unconditionally
    # under Path.home() disagrees with systemctl on any machine where
    # $XDG_CONFIG_HOME points elsewhere, leaving `enable --now` unable to find
    # the unit this just wrote.
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "systemd" / "user" / _SYSTEMD_UNIT


def _write_launchd_plist(kraft_bin: str) -> Path:
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Built with `plistlib`, not string-formatted XML: a `&` or a `<` in
    # PATH or KRAFT_HOME would otherwise produce a plist launchd cannot parse.
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": _LAUNCHD_LABEL,
                "ProgramArguments": [kraft_bin, "admin", "start"],
                "KeepAlive": True,
                "RunAtLoad": True,
                "EnvironmentVariables": _instance_env(),
            }
        )
    )
    return path


def _write_systemd_unit(kraft_bin: str) -> Path:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # systemd reads the quoted value with C-style escapes, so a literal
    # backslash or double quote in PATH/KRAFT_HOME has to be escaped or the
    # unit fails to parse.
    def escaped(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    env_lines = "".join(
        f'Environment="{key}={escaped(value)}"\n' for key, value in _instance_env().items()
    )
    path.write_text(
        "[Unit]\n"
        "Description=Kraft orchestrator daemon\n"
        "\n"
        "[Service]\n"
        f"ExecStart={kraft_bin} admin start\n"
        "Restart=always\n"
        "RestartSec=2\n"
        f"{env_lines}"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    return path


def _cmd_install_service(ns: argparse.Namespace) -> None:
    """Adopt or refuse, never start a second one on the same run dir (spec
    §1) -- refuse: there is no supported way to hand an already-running,
    unmanaged process to the service manager without evicting or faking it.
    """
    running = _read_pid(_pid_path())
    if running is not None:
        raise SystemExit(
            f"kraft admin install-service: a server is already running (pid {running}) - "
            "`kraft admin stop` first, then install the service so it starts the next one."
        )
    kraft_bin = _kraft_executable()
    if sys.platform == "darwin":
        path = _write_launchd_plist(kraft_bin)
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
        manager = "launchctl"
    elif sys.platform.startswith("linux"):
        path = _write_systemd_unit(kraft_bin)
        # `enable` by the bare unit name only resolves against the *running
        # manager's own* unit search path -- fixed to the environment the
        # manager was started with, not this process's. On a machine whose
        # $HOME the manager didn't start with (any sandboxed test, notably
        # GitHub Actions' persistent per-job user session), that search path
        # will never contain what we just wrote, so `daemon-reload` followed
        # by `enable --now <bare name>` fails "Unit file ... does not exist"
        # every time, not intermittently (Kraft-1zvs3 -- confirmed by
        # instrumenting the manager's own environment in CI). Passing the
        # absolute path instead makes `enable` link the file into the
        # manager's real unit directory itself (the documented way to adopt
        # an out-of-tree unit), sidestepping that mismatch entirely.
        subprocess.run(["systemctl", "--user", "enable", "--now", str(path)], check=True)
        manager = "systemctl --user"
    else:
        raise SystemExit(f"kraft admin install-service: unsupported platform {sys.platform}")
    print(f"kraft: wrote {path}, loaded with {manager}")


def _cmd_uninstall_service(ns: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        path = _launchd_plist_path()
        if path.is_file():
            subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
            path.unlink()
        print(f"kraft: removed {path}")
    elif sys.platform.startswith("linux"):
        path = _systemd_unit_path()
        subprocess.run(["systemctl", "--user", "disable", "--now", _SYSTEMD_UNIT], check=False)
        if path.is_file():
            path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print(f"kraft: removed {path}")
    else:
        raise SystemExit(f"kraft admin uninstall-service: unsupported platform {sys.platform}")


def adopt_pre_2_home(in_use: Path) -> bool:
    """The 2.0 rename: a home whose config still sits in 1.x's `templates/`
    becomes `config/`, once, before anything reads it. True if it moved
    anything.

    `in_use` is the directory this process reads (`paths.config_dir()`). Only
    a home at the default location is adopted, so `in_use` must be
    `$KRAFT_HOME/config` or `$KRAFT_HOME/templates` itself: 1.4's
    `install-service` wrote `KRAFT_TEMPLATES_DIR` into the unit with exactly
    that default, and the rename must follow it, not seed an empty `config/`
    beside it. A directory pointed at anywhere else is the operator's to name.
    `templates/` must be a 1.x home (a `library.yaml`) or a 0.x one (a
    `registry.yaml`, which `kraft admin update` then replaces where it now is).

    One rename, then `templates` is left as a relative link to `config`: a 1.4
    process still running (an MCP server under an agent, 1.4's own
    `update --restart` waiting on the port) keeps finding its `access.yaml`,
    and a rollback to 1.4 finds the home where it looks. A `config/` already
    made before this start (the harness guide's `mkdir -p
    ~/.kraft/config/harnesses`) holding no `library.yaml` gets each entry it
    lacks moved in instead; an entry both hold is left in `templates/`, and
    `kraft admin doctor` names both directories. A rename the filesystem
    refuses is reported and the home read where it is (`paths.config_dir`).
    The keys 2.0 moved between files are `carry_moved_keys`'s, run by the
    same start."""
    named_2 = os.environ.get("KRAFT_CONFIG_DIR")
    if named_2 and not names_default_config_dir(named_2):
        # A 2.0 name, set on purpose: the directory it names is the
        # operator's, never renamed, merged into or refused, wherever it is.
        # Naming the default itself is the default: nothing there to protect.
        return False
    home = default_config_dir()
    old = home.with_name("templates")
    # A 0.x update interrupted between its two renames left the home staged
    # under the old name: finish it there first, or it is stranded.
    finish_interrupted_update(old)
    if old.is_symlink() or not old.is_dir():
        return False
    if os.path.realpath(in_use) not in (os.path.realpath(home), os.path.realpath(old)):
        return False
    if pre_2_config_dir() != old:
        return False
    # Named by the 1.x variable, not merely read in the window before this
    # rename (`paths.config_dir` reads `templates/` until `config/` has a
    # library): only a variable keeps reading it after the merge.
    named = os.environ.get(LEGACY_CONFIG_DIR_VAR)
    reads_old = bool(named) and os.path.realpath(named) == os.path.realpath(old)
    if home.exists() or home.is_symlink():
        if not home.is_dir() or home.is_symlink() or (home / "library.yaml").exists():
            return False
        if reads_old:
            # A merge could leave a clash behind in `templates/`, which this
            # process would then read without its library and seed over.
            raise SystemExit(
                f"kraft: {old} (named by KRAFT_TEMPLATES_DIR) and {home} both exist. "
                f"Merge what {old} holds into {home} and remove {old}, then unset "
                f"KRAFT_TEMPLATES_DIR or point it at {home}; or remove {home} if it holds "
                "nothing you need. Then start Kraft again."
            )
    try:
        if home.exists():
            moved = _merge_into(old, home)
        else:
            old.rename(home)
            moved = [f"moved {old} to {home}"]
    except OSError as e:
        print(
            f"kraft: could not move {old} to {home} ({e}); reading it where it is. "
            "Move it by hand with the server stopped.",
            file=sys.stderr,
        )
        return False
    if not old.exists():
        try:
            old.symlink_to(home.name, target_is_directory=True)
        except OSError as e:
            print(
                f"kraft: moved {old} to {home}, but could not leave a link at {old} ({e}); "
                "a 1.4 process still running reads its old path",
                file=sys.stderr,
            )
    for line in moved:
        print(f"kraft: {line}: the config directory is config/ since 2.0", file=sys.stderr)
    if old.is_dir() and not old.is_symlink():
        left = ", ".join(sorted(p.name for p in old.iterdir()))
        print(
            f"kraft: {old} still holds {left}, which {home} also has; "
            "merge them by hand (kraft admin doctor names both)",
            file=sys.stderr,
        )
    return True


def _merge_into(old: Path, home: Path) -> list[str]:
    """Move each entry of `old` that `home` lacks (or holds only as an empty
    directory) into `home`; `old` is removed once nothing is left in it. An
    empty directory in `old` holds nothing to merge and is removed, so it
    does not keep `old` from becoming the link (R13c-05)."""
    moved = []
    for entry in sorted(old.iterdir()):
        target = home / entry.name
        if entry.is_dir() and not entry.is_symlink() and not any(entry.iterdir()):
            entry.rmdir()
            continue
        if target.is_dir() and not target.is_symlink() and not any(target.iterdir()):
            target.rmdir()
        if target.exists() or target.is_symlink():
            continue
        entry.rename(target)
        moved.append(f"moved {entry} to {target}")
    if not any(old.iterdir()):
        old.rmdir()
    return moved


def _stayed(triggers: list) -> str:
    """Which of the `policy.yaml` triggers a start left there are still read
    and which `policy._triggers` skips, and why, numbered as the file then
    holds them."""
    from kraft.policy import skipped_trigger

    read, skipped = [], {}
    for index, trigger in enumerate(triggers):
        if note := skipped_trigger(trigger):
            skipped.setdefault(note, []).append(f"triggers.{index}")
        else:
            read.append(f"triggers.{index}")
    parts = [f"{', '.join(read)} still read"] if read else []
    parts += [f"{', '.join(names)} skipped, {note}" for note, names in skipped.items()]
    return "; ".join(parts)


def carry_moved_keys(config_dir: Path) -> list[str]:
    """Move what 2.0 reads from another file: `intake.yaml`'s `max_concurrent`
    into `policy.yaml` (unless it already sets one), and `policy.yaml`'s
    `triggers:` onto `intake.yaml`'s `schedules:`. Run by every start, in
    whichever directory is in use; a home with nothing left to move is not
    written. Returns a line per move.

    Each file is rewritten over its own text (`drafts.preserve`), so its
    comments stay, and through a symlink to wherever the file really is; a
    moved trigger takes its own comments with it. A trigger moves only if
    2.0's schedule takes it: 1.4 ignored a key it refuses (an `enabled:
    false`), so that one stays, still read and fired; the line says why, and
    which of those that stay are read and which skipped (`_stayed`). The
    rest move. None move unless `intake.yaml` then still loads
    (`config.Intake`): one that fails turns auto-intake off with every
    schedule in it. Only the entries `schedules:` lacks are added, compared
    as schedules (a missing `description` is an empty one), and
    `intake.yaml` is written first: a start interrupted between the two
    writes moves nothing twice. A file that does not parse is left alone,
    and its reader says why. One whose rewrite would not read back as
    itself is written without its comments, and the line says so
    (`preserve.RewriteError`)."""
    import pydantic
    import yaml

    from kraft.config import Intake, Schedule, schedule_refusal, write_text
    from kraft.drafts import authored, preserve

    def schedule(entry: object) -> dict | None:
        try:
            return Schedule.model_validate(entry).model_dump()
        except pydantic.ValidationError:
            return None

    def load(name: str) -> tuple[Path, str, dict] | None:
        path = Path(os.path.realpath(config_dir / name))
        try:
            text = path.read_text() if path.is_file() else ""
            data = yaml.safe_load(text) if text.strip() else {}
        except (OSError, ValueError, yaml.YAMLError):
            return None
        # A file of only comments reads as empty, as `config.read_yaml` reads it.
        data = {} if data is None else data
        return (path, text, data) if isinstance(data, dict) else None

    moved: list[str] = []
    intake, policy = load("intake.yaml"), load("policy.yaml")
    if intake is None or policy is None:
        return moved
    (intake_path, intake_text, intake_data), (policy_path, policy_text, policy_data) = (
        intake,
        policy,
    )
    new_intake, new_policy = dict(intake_data), dict(policy_data)
    if "max_concurrent" in new_intake:
        value = new_intake.pop("max_concurrent")
        if "max_concurrent" not in new_policy and isinstance(value, int):
            new_policy["max_concurrent"] = value
            moved.append(
                f"intake.yaml: max_concurrent {value} moved to policy.yaml, which reads it"
            )
        else:
            moved.append("intake.yaml: dropped max_concurrent; policy.yaml's is the one read")
    triggers = new_policy.get("triggers")
    if isinstance(triggers, list) and triggers:
        have = new_intake.get("schedules")
        have = have if isinstance(have, list) else []
        seen = [schedule(h) for h in have]
        stay = [t for t in triggers if schedule(t) is None]
        add, dupes = [], 0
        for trigger in triggers:
            if (as_schedule := schedule(trigger)) is None:
                continue
            if as_schedule in seen:
                dupes += 1
                continue
            seen.append(as_schedule)
            add.append(trigger)
        candidate = {**new_intake, "schedules": [*have, *add]}
        try:
            Intake.model_validate(candidate)
        except pydantic.ValidationError as e:
            why = e.errors()[0]
            where = ".".join(str(p) for p in why["loc"])
            moved.append(
                f"policy.yaml: triggers left where they are ({_stayed(triggers)}): "
                f"intake.yaml would not load with them ({where}: {why['msg']})"
            )
        else:
            nodes = preserve.carried(policy_text, "triggers")
            if len(nodes) == len(triggers):
                # The same entries as ruamel nodes, so each keeps its comments.
                add = [nodes[triggers.index(t)] for t in add]
                candidate = {**new_intake, "schedules": [*have, *add]}
            if add:
                new_intake = candidate
            if stay:
                new_policy["triggers"] = stay
            else:
                del new_policy["triggers"]
            if add or dupes:
                moved.append(
                    f"policy.yaml: {len(add)} trigger(s) moved to intake.yaml's schedules"
                    + (f" ({dupes} already there)" if dupes else "")
                )
            if stay:
                moved.append(
                    f"policy.yaml: {len(stay)} trigger(s) left where they are "
                    f"({_stayed(stay)}): intake.yaml's schedules refuse them "
                    f"({schedule_refusal(stay, 'triggers')}); fix that and the next start "
                    "moves them"
                )
    elif isinstance(triggers, list) or ("triggers" in new_policy and triggers is None):
        # `triggers:` with nothing under it is the header left behind once the
        # entries were deleted: dropped like an empty list (R14c-02).
        del new_policy["triggers"]
        moved.append("policy.yaml: dropped an empty triggers list; schedules are intake.yaml's")

    def text_of(name: str, text: str, new: dict, offset: int = 0) -> str:
        try:
            return preserve.rewrite(text, new, list_offset=offset)
        except preserve.RewriteError as exc:
            # Written over its own text it would not read back as itself
            # (R13d-01): a file without its comments beats one Kraft refuses.
            moved.append(f"{name}: written without its comments: keeping them, {exc}")
            return authored.dump(preserve.plain(new))

    # Both texts are made before either is written. A moved list keeps the
    # indent it had in policy.yaml.
    writes = []
    if new_intake != intake_data:
        offset = preserve.list_offset(policy_text)
        writes.append((intake_path, text_of("intake.yaml", intake_text, new_intake, offset)))
    if new_policy != policy_data:
        writes.append((policy_path, text_of("policy.yaml", policy_text, new_policy)))
    for path, text in writes:
        write_text(path, text)
    return moved


def seed_home(templates_dir: Path) -> bool:
    """Copy the packaged config into a home that has none. True if it seeded.

    A 1.x home is adopted first (`adopt_pre_2_home`), so its `templates/` is
    never re-seeded beside a fresh `config/`.

    Only ever creates. An upgrade must not overwrite a policy the operator
    edited, and `access.yaml` is never bundled — it holds a password hash and a
    bind address that belong to one machine. `notify.yaml` is never bundled
    either, for the same reason: it usually holds a webhook URL with a bearer
    token embedded, and that belongs to one machine too. Reachable today by
    anyone who points `KRAFT_CONFIG_DIR` at a checkout with a live
    notify.yaml and runs `just install` -- if either file ever slips into
    `BUNDLED / "config"`, it must still not reach a seeded home.

    A directory made before the first start, holding only what the docs say
    to write there (a `sandbox.yaml`, a `detectors.yaml`), has no
    `library.yaml`: each bundled file it lacks is added beside the ones it
    has, and the whole directory is then the operator's alone, as a seeded
    one is. A home with a `library.yaml` was seeded, and one with a legacy
    registry is a pre-V1 home for `kraft admin update` (adopted under its 2.0
    name first, so that command finds it); neither is touched.
    Nor is a directory holding none of Kraft's config names: a mistyped
    `KRAFT_CONFIG_DIR` must not fill some other directory with it.
    """
    from kraft.templates.library import LIBRARY_FILE, is_pre_v1

    if adopt_pre_2_home(templates_dir):
        return False
    if finish_interrupted_update(templates_dir):
        return False
    if templates_dir.exists() and (
        (templates_dir / LIBRARY_FILE).exists() or is_pre_v1(templates_dir)
    ):
        return False
    if not (BUNDLED / "config").is_dir():
        if templates_dir.exists():
            return False  # a config directory pointed at by hand, in a source checkout
        raise SystemExit(
            f"kraft: no config in {templates_dir} and no bundled defaults to seed it "
            "with. This build shipped without them — reinstall with `just install`, "
            "or point KRAFT_CONFIG_DIR at a config directory."
        )
    if templates_dir.exists() and not _holds_config(templates_dir):
        return False
    # Build beside the target and rename: an interrupted copy must not leave a
    # half-seeded home that every later start then treats as already seeded.
    staging = _stage_bundle(templates_dir)
    if not templates_dir.exists():
        staging.rename(templates_dir)
        return True
    # Moved in one at a time, `library.yaml` last: it is what marks a home
    # seeded, so a fill cut short is finished by the next start.
    for entry in sorted(staging.iterdir(), key=lambda p: p.name == LIBRARY_FILE):
        _move_missing(entry, templates_dir / entry.name)
    shutil.rmtree(staging, ignore_errors=True)
    _operators_alone(templates_dir)
    return True


def _holds_config(directory: Path) -> bool:
    """Empty (dotfiles aside), or holding at least one name Kraft's config
    uses: a templates directory someone started, not an unrelated one."""
    names = {p.name for p in directory.iterdir() if not p.name.startswith(".")}
    known = {p.name for p in (BUNDLED / "config").iterdir()}
    return not names or bool(names & (known | set(MACHINE_CONFIG) | CONFIG_EXTRAS))


#: Config files a templates directory can hold that are neither bundled nor
#: carried across a major update: read when present, never seeded.
CONFIG_EXTRAS = frozenset({"sandbox.yaml", "detectors.yaml", "plugins.yaml", "plugins.lock"})


def _operators_alone(root: Path) -> None:
    """`root` and everything under it the operator's alone, as every file
    Kraft saves there is (`config.write_text` creates 0600) and as `run/` is:
    directories 0700, files 0600. A symlink is left as it is."""
    root.chmod(0o700)
    for parent, dirs, files in os.walk(root):
        for name in dirs:
            path = os.path.join(parent, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)
        for name in files:
            path = os.path.join(parent, name)
            if not os.path.islink(path):
                os.chmod(path, 0o600)


def _move_missing(source: Path, target: Path) -> None:
    """Move `source` to `target` where `target` has nothing; into a directory
    that exists, each of its entries the same way. Never replaces a file."""
    if not target.exists() and not target.is_symlink():
        source.rename(target)
    elif source.is_dir() and target.is_dir():
        for entry in source.iterdir():
            _move_missing(entry, target / entry.name)


def _stage_bundle(templates_dir: Path) -> Path:
    """The bundled config, copied beside `templates_dir` under a staging name
    for the caller to rename into place. Returns the staging directory."""
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(BUNDLED / "config", staging)
    (staging / "access.yaml").unlink(missing_ok=True)
    (staging / "notify.yaml").unlink(missing_ok=True)
    # What this home was seeded from, for `capabilities.added_since` (Kraft-hxt6x).
    # Written into `staging`, before the rename, so an interrupted seed can never
    # leave a stamp describing config that is not there.
    #
    # Deliberately not `.yaml`, so nothing that globs this directory's YAML
    # ever reads the stamp as configuration.
    (staging / ".seeded-version").write_text(f"{_version()}\n")
    # The copy would otherwise carry the package's 0644 until its first save
    # from Settings.
    _operators_alone(staging)
    return staging


#: What a home holds that belongs to this machine rather than to the template
#: schema: the password hash and bind, the webhook, the theme, the connected
#: repositories, auto-intake and harness
#: overrides. A major update carries each across unchanged; everything else --
#: the registry, the chains, `policy.yaml` -- is replaced, and kept in the backup.
MACHINE_CONFIG = (
    "access.yaml",
    "notify.yaml",
    "theme.yaml",
    "repos.yaml",
    "intake.yaml",
    "harnesses",
)


#: Written into a major update's staging directory once it is complete, naming
#: the backup the old home moves to. Its presence is what tells a later start
#: that a missing home is a swap to finish, not a home to seed.
UPDATE_STAGED = ".pre-v1-update"


def replace_pre_v1_config(templates_dir: Path, backup: Path) -> CarriedPolicy | None:
    """Install the bundled V1 configuration in place of a pre-V1 home, which
    moves to `backup` whole (`major-update-preserves-replaced-configuration`).

    Nothing is converted (`migration-helper-is-not-guaranteed`): the old
    chains and registry are only in the backup. The operator's `policy.yaml`
    values move onto the V1 seed's where V1 still has the key (Ruling 172);
    the result says what was dropped, `None` if there was no readable policy.

    Crash-safe (Kraft-cttgx): the new home is built complete beside the old
    one and marked (`UPDATE_STAGED`) before anything moves. Then two renames:
    old -> backup, staged -> home. Between them the home is missing, and
    `finish_interrupted_update` -- run by the next start and the next update --
    completes the swap from the marked staging instead of seeding over it."""
    if not (BUNDLED / "config").is_dir():
        raise SystemExit(
            "kraft admin update: this build shipped no bundled configuration to install; "
            "reinstall with `just install`. Nothing was changed."
        )
    staging = _stage_bundle(templates_dir)
    for name in MACHINE_CONFIG:
        kept = templates_dir / name
        if kept.is_dir():
            # The operator's copy wins over a bundled file of the same name.
            shutil.copytree(kept, staging / name, dirs_exist_ok=True)
        elif kept.is_file():
            shutil.copy2(kept, staging / name)
    carried = _carry_policy(templates_dir / "policy.yaml", staging / "policy.yaml", backup)
    (staging / UPDATE_STAGED).write_text(f"{backup.name}\n")
    templates_dir.rename(backup)
    staging.rename(templates_dir)
    (templates_dir / UPDATE_STAGED).unlink()
    return carried


def _carry_policy(old: Path, new: Path, backup: Path) -> CarriedPolicy | None:
    try:
        legacy = yaml.safe_load(old.read_text())
        seed = yaml.safe_load(new.read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if not isinstance(legacy, dict):
        return None
    carried = CarriedPolicy.from_legacy(legacy, seed)
    if carried.data != seed:
        new.write_text(
            f"# The default policy, with the values `kraft admin update` carried over\n"
            f"# from the Kraft 0.x policy.yaml; that file is unchanged in {backup}.\n"
            + yaml.safe_dump(carried.data, sort_keys=False)
        )
    return carried


def finish_interrupted_update(templates_dir: Path) -> bool:
    """Complete a major update that stopped between its two renames: the home
    is missing and its staging is marked complete. True if it did, and says so.

    That is the only half-state the swap can leave -- the marker is written
    after the staging is whole, and the home moves only after the marker -- so
    there is nothing to roll back. An unmarked staging is an interrupted
    *seed*, and seeding over it is right."""
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    marker = staging / UPDATE_STAGED
    if templates_dir.exists() or not marker.is_file():
        return False
    backup = templates_dir.with_name(marker.read_text().strip())
    staging.rename(templates_dir)
    (templates_dir / UPDATE_STAGED).unlink()
    print(
        f"kraft: finished an interrupted update: installed the staged template configuration "
        f"in {templates_dir}; the old one is at {backup}",
        file=sys.stderr,
    )
    return True


def _warn_if_pre_v1(templates_dir: Path) -> None:
    """A legacy install's first V1 start: nothing is converted or overwritten.
    The server comes up degraded and refuses new work until the operator runs
    `kraft admin update`, and this says so where they are looking."""
    from kraft.templates.library import is_pre_v1

    if is_pre_v1(templates_dir):
        print(
            f"kraft: {templates_dir} holds a Kraft 0.x template configuration. Starting "
            "degraded, refusing new work; run `kraft admin update` to back it up and "
            "install the current one.",
            file=sys.stderr,
        )


def _bind(templates_dir: Path) -> tuple[str, int]:
    """Bind address from access.yaml — this is the "takes effect on restart" in
    Settings › Access (design 5e). Env still wins, for a one-off run."""
    access = config.Access.load(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access.bind
    port = int(os.environ.get("KRAFT_PORT") or access.port)
    if host not in config.LOOPBACK and not access.password_hash:
        # What sets one on a server still on loopback: the Access screen asks for
        # it when Local network is picked, and PUT /api/access takes it alone.
        raise SystemExit(
            f"refusing to bind {host}: no password is set. Start Kraft on loopback with "
            "`kraft admin start --host 127.0.0.1`, then set one in Settings › Access "
            "(picking Local network asks for it), or with "
            f"`curl -X PUT http://127.0.0.1:{port}/api/access "
            "-H 'Content-Type: application/json' -d '{\"password\": \"...\"}'`. "
            "See https://itsomidkarami.github.io/kraft/guides/remote-access"
        )
    return host, port


def _pid_path() -> Path:
    """Same run dir the API, the client and the doctor resolve."""
    return RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).pid


def _read_pid(path: Path) -> int | None:
    """The pid of this run dir's live server, or None - clearing a stale file.

    A pidfile that outlived its server names nothing, and its pid may since
    have gone to another process, so no caller may treat the file alone as
    "a server is running" (`kraft.pidfile` says how a pid is confirmed).
    `_refuse_if_addr_taken` closes the common two-run-dirs-one-port case
    (Kraft-kquf).
    """
    state = pidfile.read(path)
    if state.running:
        return state.pid
    if state.pid is not None or path.is_file():
        cleared = pidfile.clear_stale(path, state.pid)
        if cleared and state.why != "is not running":
            # A dead pid is the ordinary stale file and needs no word. One
            # naming somebody else's process is what this check is for.
            what = f"pid {state.pid} {state.why}" if state.pid is not None else state.why
            print(f"kraft: removed a stale pidfile: {what}", file=sys.stderr)
    return None


def _update_notice() -> None:
    """One line at boot when a newer release exists.

    Reads the 24h cache, so an ordinary start pays nothing. A cold cache on a
    machine with no route out costs `update.TIMEOUT` once a day, and
    `KRAFT_NO_UPDATE_CHECK=1` costs nothing ever.
    """
    if os.environ.get("KRAFT_NO_UPDATE_CHECK"):
        return
    from kraft import update

    release = update.latest(channel=update.channel_of(update.installed()))
    if update.is_behind(release):
        print(
            f"kraft: {update.installed()} installed, {release.tag} available - kraft admin update"
        )


_WILDCARDS = {"0.0.0.0", "::", ""}


def _probe_host(host: str) -> str:
    """127.0.0.1 is what a same-machine client actually reaches a wildcard
    bind through -- connecting a socket to 0.0.0.0 itself is not a reliable
    target on macOS or Linux."""
    return "127.0.0.1" if host in _WILDCARDS else host


def _describe_occupant(host: str, port: int) -> str:
    """Best-effort identity of whatever already answers, for the refusal
    message (spec: "exit with what answered and how to inspect it")."""
    probe = _probe_host(host)
    try:
        response = httpx.get(f"http://{config.url_host(probe)}:{port}/api/health", timeout=1.0)
        data = response.json()
        return f"a Kraft server (run_dir {data.get('run_dir')}, pid {data.get('pid')})"
    except Exception:
        return "something"


def _refuse_if_addr_taken(host: str, port: int) -> None:
    """Before binding, connect to host:port: if something answers, refuse
    with what it is and how to inspect it, rather than letting uvicorn's
    bind either fail cryptically or -- the macOS "127.0.0.1 next to *:"
    case -- succeed alongside it (Kraft-kquf, admin half).
    """
    probe = _probe_host(host)
    try:
        with socket.create_connection((probe, port), timeout=0.5):
            pass
    except OSError:
        return
    occupant = _describe_occupant(host, port)
    where = f"{config.url_host(probe)}:{port}"
    raise SystemExit(
        f"kraft: refusing to start - {occupant} is already answering on "
        f"{where}. curl http://{where}/api/health to inspect "
        "it, or `kraft admin stop` if it's yours."
    )


def _rotate_if_large(log_path: Path, max_bytes: int = 8_000_000) -> None:
    """Rotate before each start, not continuously (see the plan's ponytail
    note) -- server.log was 176KB and unbounded (Kraft-mqwg)."""
    try:
        if log_path.stat().st_size <= max_bytes:
            return
    except FileNotFoundError:
        return
    backup = log_path.with_suffix(log_path.suffix + ".1")
    backup.unlink(missing_ok=True)
    log_path.rename(backup)


class _Tee:
    """Every write goes to both streams -- the log file, and whatever
    `sys.stdout`/`sys.stderr` was before this replaced it (the real
    terminal, for a foreground start).
    """

    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        # The first stream is the real terminal (or whatever sys.stdout/
        # stderr already was) -- uvicorn checks this to decide whether to
        # colorize its own log output.
        return self._streams[0].isatty()


def _redirect_output_to_log(log_path: Path) -> None:
    """A foreground start writes to server.log too now, not only --detach
    (Kraft-mqwg) -- *too*, per spec §3: a human running bare `kraft`, or
    `just dev`, still has to see the URL line, shutdown reason, tracebacks
    and uvicorn errors on their own terminal, not just in the file.

    Replaces the `sys.stdout`/`sys.stderr` objects rather than dup2'ing the
    fds: `logging.lastResort` (the fallback handler behind every existing
    `logging.warning(...)` call in the app) and every `print(..., file=...)`
    call here resolve `sys.stderr` dynamically at write time, not once at
    import, and uvicorn configures its own log handlers even later, once
    `server.run()` starts -- so all three pick up the replacement with no
    changes anywhere else, and land in both places instead of only the file.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)


class _SignalLoggingServer(uvicorn.Server):
    """The next silent death names itself (Kraft-mqwg): `_serve`'s `finally`
    already ran on the way out and said nothing about why. `handle_exit` is
    uvicorn's own signal callback -- log before deferring to its usual
    graceful shutdown.

    Deliberately does *not* clear the pidfile here. `handle_exit` runs at
    signal *delivery*, and uvicorn then drains for up to
    `timeout_graceful_shutdown` with the databases still open; a pidfile
    removed at delivery would make `_cmd_stop` print "stopped" while the old
    process is still alive, and would let a second `_serve` past both the
    pidfile check and the address probe (uvicorn closes the listening socket
    first) onto the same run dir. `_serve`'s `_exit_on_signal` clears it once
    the drain is over.
    """

    def handle_exit(self, sig, frame):
        print(f"kraft: shutdown - signal {signal.Signals(sig).name}", file=sys.stderr, flush=True)
        super().handle_exit(sig, frame)


def prepare_home() -> Path:
    """The config directory this server runs against, after the one-time 2.0
    moves and the seed, in this order: the rename of a 1.x home at its
    default location (`adopt_pre_2_home`), the packaged defaults into a home
    that has none (`seed_home`), and the two keys 2.0 reads from another file
    (`carry_moved_keys`), in whichever directory is in use, so a home an
    operator points at by hand gets them too.

    Called only once this process knows no server is running. A 1.4 server
    still up re-reads `repos.yaml` per request, so a rename under it would
    lose its repos and fail its running items, and the 2.0 start would then
    exit "already running" anyway. Until that check every reader finds a 1.x
    home under its old name (`paths.config_dir`), which is where `_bind` and
    the address probe read it."""
    adopt_pre_2_home(config_dir())
    templates_dir = config_dir()
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    _warn_if_pre_v1(templates_dir)
    if linked := linked_pre_2_config_dir():
        print(f"kraft: {linked_advice(linked)}", file=sys.stderr)
    for line in carry_moved_keys(templates_dir):
        print(f"kraft: {line}", file=sys.stderr)
    return templates_dir


def _serve() -> None:
    templates_dir = config_dir()
    pid_path = _pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = RunDirs(pid_path.parent).logs / "server.log"
    if not os.environ.get("KRAFT_LOG_REDIRECTED"):
        # Before _bind/_refuse_if_addr_taken/the already-running exit, not
        # after: a supervised daemon (launchd KeepAlive / systemd
        # Restart=always, task 5) has no console and launchd's plist sets no
        # StandardOutPath/StandardErrorPath, so a failure on any of those
        # paths used to vanish into /dev/null and retry forever with no trace
        # anywhere (Kraft-mqwg). --detach's parent (_start_detached) already
        # ties fd 1/2 to this same file before spawning.
        _rotate_if_large(log_path)
        _redirect_output_to_log(log_path)
    host, port = _bind(templates_dir)
    _refuse_if_addr_taken(host, port)
    running = _read_pid(pid_path)
    # The lock, not the read, is what keeps a second server out: two starts
    # racing past the read cannot both take it. It is held until this process
    # exits, however it exits (`kraft.pidfile`).
    held = pidfile.hold(pid_path) if running is None else None
    if held is None:
        # Two servers on one run dir share databases and worktrees, and only
        # collide on the port if they were given the same one.
        running = running or pidfile.read(pid_path).pid
        print(f"kraft: already running (pid {running}) - kraft admin stop", file=sys.stderr)
        raise SystemExit(1)
    # The lock is held: the home is this server's to rename, seed and move
    # keys in. Its `access.yaml` is read again from where it now is, in case
    # the move left a different one in use (R12c-01).
    if (host, port) != (moved := _bind(prepare_home())):
        host, port = moved
        _refuse_if_addr_taken(host, port)

    def _exit_on_signal(sig, frame):
        # uvicorn re-raises the SIGTERM it drained on, with this handler put
        # back, so the `finally` below never runs on the most common way a
        # server stops (`kraft admin stop`, a service manager, a reboot).
        # Clear the files here, then die by the signal as before: a service
        # manager reads that as a clean stop. A SIGTERM before uvicorn's
        # own handlers are in lands here too.
        pid_path.unlink(missing_ok=True)
        RunDirs(pid_path.parent).mode.unlink(missing_ok=True)
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    previous = signal.signal(signal.SIGTERM, _exit_on_signal)
    # So `kraft admin restart` starts it back up the same way it was running:
    # `_start_detached` marks its child with KRAFT_DETACHED before exec'ing
    # into this same function.
    RunDirs(pid_path.parent).mode.write_text(
        "detached" if os.environ.get("KRAFT_DETACHED") else "attached"
    )
    # Every worker Kraft launches inherits these two names (they're in
    # `worker_env.BASELINE`, which `adapters/subprocess.py`'s `full_env`
    # copies out of `os.environ`): a leftover process found on a port can be
    # checked against the daemon it actually is, rather than assumed stale
    # and killed (Kraft-f8u3).
    os.environ["KRAFT_DAEMON_PID"] = str(os.getpid())
    os.environ["KRAFT_DAEMON_PORT"] = str(port)
    _update_notice()
    print(f"kraft: http://{config.url_host(host)}:{port}")
    try:
        server = _SignalLoggingServer(
            uvicorn.Config(
                "kraft.api:app",
                host=host,
                port=port,
                log_level="warning",
                # A backstop, not the fix: the fix is `ws_events` returning when
                # the connection goes away (Kraft-9oab). This bounds the next
                # endpoint that forgets, and an in-flight HTTP request that
                # hangs. 10s is comfortably above the slowest ordinary request
                # (a forge call).
                timeout_graceful_shutdown=10,
            )
        )
        server.run()
    except BaseException:
        print("kraft: shutdown - unhandled exception:", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise
    finally:
        pid_path.unlink(missing_ok=True)
        RunDirs(pid_path.parent).mode.unlink(missing_ok=True)
        os.close(held)
        signal.signal(signal.SIGTERM, previous)


def _cmd_start(ns: argparse.Namespace) -> None:
    """The same path as bare `kraft`, with three flags on top.

    The host/port flags become the env vars `_bind()` already reads, so there
    is one precedence chain (flag > env > access.yaml) and one place that
    refuses a LAN bind without a password. Setting the env rather than passing
    arguments through is deliberate: a second signature would be a second
    place for that check to be forgotten.
    """
    if ns.host:
        # Same gap as --port below. A wildcard bind is dialled on loopback
        # (`client.base_url`), so only a different concrete host needs saying.
        dialled = httpx.URL(client.base_url()).host
        os.environ["KRAFT_HOST"] = ns.host
        if ns.host not in (dialled, "0.0.0.0", "::"):
            print(
                f"kraft: other kraft commands still dial host {dialled}; reach this "
                f"instance with KRAFT_HOST={ns.host}, or set `bind: {ns.host}` in access.yaml"
            )
    if ns.port:
        # Not written to access.yaml: a flag silently rewriting config would
        # surprise more than it helps. Say so instead, while the port every
        # other verb dials is still the one without the flag.
        dialled = httpx.URL(client.base_url()).port
        os.environ["KRAFT_PORT"] = str(ns.port)
        if ns.port != dialled:
            print(
                f"kraft: other kraft commands still dial port {dialled}; reach this "
                f"instance with KRAFT_PORT={ns.port}, or set `port: {ns.port}` in access.yaml"
            )
    if ns.detach:
        _start_detached()
        return
    _serve()


def _start_detached() -> None:
    """Launch `admin start` in its own session and return once it is up.

    Not this process backgrounding itself: once `_serve()` calls into uvicorn
    it owns signal handling and stdio for good, so there is no point after
    that where control could still return to a caller. A real child, in its
    own session (`start_new_session`) so it outlives the shell that launched
    it, with stdio redirected to the same place worker logs go. It writes the
    same pidfile `_serve` always has, so `admin stop`/`health`/`doctor` never
    need to know a server was started this way.
    """
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).ensure()
    pid_path = run_dirs.pid
    running = _read_pid(pid_path)
    if running is not None:
        print(f"kraft: already running (pid {running}) - kraft admin stop", file=sys.stderr)
        raise SystemExit(1)
    # Not prepared here: two detached starts racing past this read would both
    # rename and seed. The child's `_serve` prepares the home once it holds
    # the lock (`prepare_home`); until then the home is read where it is.
    templates_dir = config_dir()
    # Resolved (and validated — refuses a password-less LAN bind) here too, so
    # a bad access.yaml fails this shell instead of showing up only as a child
    # that exited before ever writing a pidfile.
    host, port = _bind(templates_dir)
    _refuse_if_addr_taken(host, port)

    log_path = run_dirs.logs / "server.log"
    _rotate_if_large(log_path)
    with open(log_path, "ab") as log_file:
        # Where this start's output begins: anything before it is an earlier run's.
        start_offset = log_file.tell()
        proc = subprocess.Popen(
            # `-P`: plain `-m` puts this shell's cwd first on `sys.path`, and a
            # file there named like a module Kraft imports would run instead.
            [sys.executable, "-P", "-m", "kraft", "admin", "start"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env={**os.environ, "KRAFT_LOG_REDIRECTED": "1", "KRAFT_DETACHED": "1"},
        )

    # Wait for the child to actually answer, not just fork or write its
    # pidfile: the pidfile lands before uvicorn binds, so returning on it
    # alone raced `kraft repo connect` against a server not yet listening
    # (#260's smoke test; Kraft-9efnk.22). A bad config or a port already in
    # use both exit within the first second, and returning before that would
    # report success for a server that's already dead.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = _read_pid(pid_path)
        if pid is not None:
            try:
                asyncio.run(client.health())
            except Exception:
                pass
            else:
                print(
                    f"kraft: http://{config.url_host(host)}:{port} "
                    f"(pid {pid}, detached - kraft admin stop)"
                )
                return
        if proc.poll() is not None:
            print(_detached_failure(log_path, start_offset), file=sys.stderr)
            raise SystemExit(1)
        time.sleep(0.1)
    print(f"kraft: detached start did not come up within 10s - check {log_path}", file=sys.stderr)
    raise SystemExit(1)


#: An exception's last line in a traceback: `RuntimeError: ...`,
#: `sqlite3.OperationalError: ...`. Not `SystemExit`, which only says that
#: uvicorn gave up after the error that matters.
_EXCEPTION_LINE = re.compile(r"^(?:\w+\.)*\w*(?:Error|Exception): .")


def _detached_failure(log_path: Path, start_offset: int, tail_chars: int = 2000) -> str:
    """What a detached start that exited prints: the error first, then the
    end of its output, then where the rest is.

    The error has to be found, not left to the tail: a failed lifespan logs
    its traceback, then uvicorn's own exit adds a second one, and with
    Python 3.13's and 3.14's `~~~^^^` marker lines that second traceback alone
    fills the tail, so `database schema vN is newer than code vM` scrolled
    out of it."""
    with open(log_path, "rb") as f:
        f.seek(start_offset)
        output = f.read().decode(errors="replace")
    errors = [ln for ln in output.splitlines() if _EXCEPTION_LINE.match(ln)]
    head = "kraft: detached start failed" + (f": {errors[-1]}" if errors else ":")
    return f"{head}\n{output[-tail_chars:]}\nkraft: the whole log is {log_path}"


#: How long `stop` and `restart` wait for the server to list its active items
#: before they go ahead without the list.
_LIST_TIMEOUT = 2.0


def _confirm_running_agents(
    ns: argparse.Namespace, doing: str, *, ask: bool, declined: str = "nothing was restarted"
) -> None:
    """Name the active items before a stop ends their agents, and, with `ask`,
    let a person at a terminal back out.

    A clean stop cancels every item's task, and the task kills its agent's
    process group on the way out, so the next start finds the session dead
    and stops the item (`reattach`). Without a terminal, or with `--yes`,
    this only warns: a script must not hang on a question. `declined` is what
    answering no left undone; answering no exits 1. A server that does
    not answer has nothing to list, and one that accepts the connection but
    never replies gets `_LIST_TIMEOUT`, not the client's 30 s: a wedged server
    is the usual reason to stop one, and the stop must not wait on it."""
    try:
        items = asyncio.run(
            asyncio.wait_for(client.list_work_items(WorkItemStatus.ACTIVE), timeout=_LIST_TIMEOUT)
        )
    except TimeoutError:
        print(
            f"kraft: the server did not list its active items within {_LIST_TIMEOUT:g}s; "
            "any agent it is running ends with it",
            file=sys.stderr,
        )
        return
    except Exception:
        return
    if not items:
        return
    print(
        f"kraft: {doing} the server ends the agent of any active item ({len(items)}):",
        file=sys.stderr,
    )
    for item in items:
        print(
            f"  {item['id']}  {item.get('current_node_id') or '-'}  {item['title']}",
            file=sys.stderr,
        )
    print(
        "  each one stops when Kraft starts again; retry it then. To avoid that, "
        "pause them first (kraft item pause ID) and resume them after.",
        file=sys.stderr,
    )
    if not ask or getattr(ns, "yes", False) or not sys.stdin.isatty():
        return
    if _ask("Go on? [y/N] ").strip().lower() not in ("y", "yes"):
        print(f"kraft: {declined}", file=sys.stderr)
        raise SystemExit(1)


def _ask(question: str) -> str:
    """The answer to `question`, asked on stderr beside the listing it
    follows. `input()` writes its prompt to stdout, so `kraft admin restart >
    log` sat waiting on a question that went into the file."""
    print(question, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline()


def _cmd_stop(ns: argparse.Namespace, *, warn: bool = True) -> None:
    """SIGTERM to the pid in the run dir, then wait for it to actually go.

    Nothing running is not a failure: `kraft admin stop` in a teardown script
    has to be safe to run twice.
    """
    pid_path = _pid_path()
    pid = _read_pid(pid_path)
    if pid is None:
        print("kraft: no server running")
        return
    if warn:
        _confirm_running_agents(ns, "stopping", ask=False)
    os.kill(pid, signal.SIGTERM)
    # 15s, not 5: the poll must not expire before the graceful-shutdown backstop
    # it is waiting on (`_serve`, timeout_graceful_shutdown=10).
    #
    # `_read_pid` returns None only once the pid it names is actually gone
    # (it signals 0 on every read), so this waits for the real exit, not for
    # a file to disappear -- the server holds its databases open for the
    # whole graceful drain. A supervised daemon (launchd KeepAlive / systemd
    # Restart=always, task 5) rewrites the pidfile with a fresh pid well
    # inside this poll, which is the other way out.
    for _ in range(150):
        current = _read_pid(pid_path)
        if current is None or current != pid:
            print(f"kraft: stopped (pid {pid})")
            return
        time.sleep(0.1)
    print(f"kraft: pid {pid} did not stop within 15s", file=sys.stderr)
    raise SystemExit(1)


def _service_installed() -> bool:
    if sys.platform == "darwin":
        return _launchd_plist_path().is_file()
    if sys.platform.startswith("linux"):
        return _systemd_unit_path().is_file()
    return False


def _restart_service() -> None:
    """Through the service manager, not SIGTERM+start -- a unit's own
    `restart` verb is what keeps it a *managed* restart (systemd resets its
    failure-count window; launchd has no equivalent, so unload+load is the
    closest same thing) instead of one this process fakes by racing
    KeepAlive/Restart=always for who starts the next process."""
    if sys.platform == "darwin":
        path = _launchd_plist_path()
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    else:
        subprocess.run(["systemctl", "--user", "restart", _SYSTEMD_UNIT], check=True)
    print("kraft: restarted the service")


def _cmd_restart(ns: argparse.Namespace) -> None:
    """`stop` then `start` again the same way it was running.

    A service (launchd/systemd) is restarted through its own manager, service
    file untouched, never SIGTERM'd and left to KeepAlive/Restart=always to
    notice -- explicit beats a race with the supervisor. Otherwise: stop, then
    bring it back detached if it was detached. A server running attached to
    someone's terminal can't be handed back to that terminal from here, so
    this only stops it and says so -- restarting it is that terminal's job.
    """
    _confirm_running_agents(ns, "restarting", ask=True)
    _refuse_restart_with_no_server()
    _restart(ns)


def _refuse_restart_with_no_server() -> None:
    """`kraft admin restart` with nothing to restart. Not a quiet success: a
    script running `restart` wants a server after it. `update --restart`
    does not ask this; with no server, it has nothing to restart."""
    if not _service_installed() and _read_pid(_pid_path()) is None:
        print(
            "kraft: no server running, so nothing was restarted - start it: kraft admin start",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _restart(ns: argparse.Namespace) -> None:
    """`_cmd_restart` once its question about running agents is answered:
    `update --restart` asks it before installing, not after."""
    if _service_installed():
        _restart_service()
        return
    pid_path = _pid_path()
    pid = _read_pid(pid_path)
    if pid is None:
        print("kraft: no server running")
        return
    mode_path = RunDirs(pid_path.parent).mode
    detached = mode_path.is_file() and mode_path.read_text().strip() == "detached"
    _cmd_stop(ns, warn=False)
    if detached:
        _start_detached()
    else:
        print(
            "kraft: was running attached to a terminal - start it again there: kraft",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _render_health(payload: dict) -> str:
    return render.health_block(payload)


def _cmd_health(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.health())
    common.emit(payload, _render_health, ns.json)
    if payload.get("status") != "ok":
        # exit 1 so `kraft health && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _cmd_doctor(ns: argparse.Namespace) -> None:
    from kraft import doctor

    rows = asyncio.run(doctor.run_checks())
    common.emit(rows, render.doctor_block, ns.json)
    if any(not row["ok"] for row in rows):
        # exit 1 so `kraft doctor && ...` works; the failures are already on stdout
        raise SystemExit(1)


def _render_reindex(result: dict) -> str:
    scope = result.get("repo") or "all repos"
    counts = ", ".join(f"{k} {v}" for k, v in result.get("stats", {}).items())
    return f"reindexed {scope}: {counts}"


def _cmd_reindex(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.reindex(ns.repo)), _render_reindex, ns.json)


def _render_reload(result: dict) -> str:
    refused = result.get("refused_policy")
    refused_intake = result.get("refused_intake")
    files = [n for n, bad in (("policy.yaml", refused), ("intake.yaml", refused_intake)) if not bad]
    also = f" and {' and '.join(files)}" if files else ""
    lines = [f"reloaded {len(result.get('valid', []))} template(s){also}"]
    for name, reason in (result.get("invalid_templates") or {}).items():
        lines.append(f"  invalid: {name}: {reason}")
    if refused:
        lines.append(f"  refused: policy.yaml: {refused} (the running policy is kept)")
    if refused_intake:
        # `ConfigError` names the file itself.
        lines.append(
            f"  refused: {refused_intake} (the running auto-intake and schedules are kept)"
        )
    return "\n".join(lines)


def _cmd_reload(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.reload_templates())
    common.emit(payload, _render_reload, ns.json)
    if (
        payload.get("invalid_templates")
        or payload.get("refused_policy")
        or payload.get("refused_intake")
    ):
        # exit 1 so `kraft admin reload && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _cmd_mcp(ns: argparse.Namespace) -> None:
    from kraft.mcp import serve_stdio

    serve_stdio()


def _cmd_permission_hook(ns: argparse.Namespace) -> None:
    """What a harness's pre-tool hook runs, as `sys.executable -I -m kraft` so it
    is the daemon's own install. Client side only: no server import, since
    the CLI waits on this for every tool call (Kraft-4in7z). Always answers:
    any failure is the translator's deny when fail-closed, no opinion else."""
    fail_closed = ns.fail_closed or os.environ.get("KRAFT_PERMISSION_FAIL_CLOSED") == "1"
    try:
        from kraft import harness as _harness

        h = _harness.load(None).valid.get(ns.harness)
        names = h.tool_names if h is not None else {}
        out, code = permission_hooks.answer_hook(
            ns.harness, sys.stdin.read(), names, fail_closed=fail_closed
        )
    except Exception as exc:  # noqa: BLE001 -- a hook must answer, whatever broke
        print(f"kraft permission-hook: {exc}", file=sys.stderr)
        out, code = permission_hooks.TRANSLATORS[ns.harness].render(
            "deny" if fail_closed else "no_opinion", "Kraft's permission hook failed"
        )
    sys.stdout.write(out)
    sys.stdout.flush()
    sys.exit(code)


def _cmd_init(ns: argparse.Namespace) -> None:
    from kraft.init import install

    for path in install(repo_scope=ns.repo):
        print(f"kraft: wrote {path}")


def _version() -> str:
    try:
        return _pkg_version("kraft-sdlc")
    except PackageNotFoundError:
        # A source checkout that was never installed still answers, rather than
        # traceback: `--version` exists to diagnose an install, so it has to
        # survive not being one.
        return "0.0.0+source"


def _accept_major_update(templates_dir: Path, assume_yes: bool) -> None:
    """Replace a pre-V1 `templates_dir` once the operator has said yes, or
    exit 1 having changed nothing (`major-update-requires-explicit-acceptance`).
    The breaking change is stated before the question is asked."""
    backup = templates_dir.with_name(
        f"{templates_dir.name}.pre-v1-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    print(
        f"kraft: {templates_dir} holds a Kraft 0.x template configuration (a hook\n"
        "registry and gate_after chains). It is not compatible with this Kraft, which\n"
        "runs only the library.yaml format 1.0 introduced, and there is no migration:\n"
        "replacing it installs the bundled configuration and moves the old one, whole,\n"
        "to a backup at\n"
        f"  {backup}\n"
        f"Carried across unchanged: {', '.join(MACHINE_CONFIG)}.\n"
        "policy.yaml keeps your value for every key the current format still has; any\n"
        "other key is dropped and listed. Your chains and registry.yaml are replaced.\n"
        "All of it stays in the backup.",
        file=sys.stderr,
    )
    # No terminal to ask on is a refusal, never a default yes.
    if not assume_yes and not (
        sys.stdin.isatty() and input("Replace it? [y/N] ").strip().lower() in ("y", "yes")
    ):
        print(
            "kraft admin update: nothing changed. Answer y, or pass -y, to replace it.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    carried = replace_pre_v1_config(templates_dir, backup)
    print(
        f"kraft: installed the bundled configuration in {templates_dir}; the old one is at {backup}"
    )
    if carried is None:
        print("kraft: policy.yaml: no readable 0.x policy; the default is installed")
    for key, value in (carried.dropped if carried else {}).items():
        print(
            f"kraft: policy.yaml: dropped {key} = {value!r} "
            "(the current format has no such key, or refuses the value)"
        )


def _confirm_dropped_plugins(version: str, assume_yes: bool) -> None:
    """Name the installed plugins `version` would stop loading and ask before
    installing it; exit 1 having installed nothing on a no."""
    from kraft.plugins import load as plugins_load

    dropped = plugins_load.left_out_by(version)
    if not dropped:
        return
    print(
        f"kraft {version} would stop loading these plugins until each publishes a version "
        "for it:\n  " + "\n  ".join(dropped),
        file=sys.stderr,
    )
    # No terminal to ask on is a refusal, never a default yes.
    if not assume_yes and not (
        sys.stdin.isatty() and input("Install it anyway? [y/N] ").strip().lower() in ("y", "yes")
    ):
        raise SystemExit("kraft admin update: nothing installed. Pass -y to install anyway.")


def _cmd_update(ns: argparse.Namespace) -> None:
    from kraft import update
    from kraft.templates.library import is_pre_v1

    # The configuration first: a pre-V1 home is what a legacy install's first V1
    # binary finds, and that binary is the one running this.
    templates_dir = config_dir()
    finish_interrupted_update(templates_dir)
    if is_pre_v1(templates_dir):
        _accept_major_update(templates_dir, ns.yes)

    # An install follows its own channel unless told otherwise: a release
    # candidate compared with the stable feed would be told v1.4.0 is the newest.
    channel = ns.channel or update.channel_of(update.installed())
    if channel != "stable" and update._is_homebrew_install():
        raise SystemExit(
            "kraft admin update: the Homebrew formula only tracks stable releases; "
            f"install --channel {channel} with `uv tool` instead."
        )
    release = update.latest(force=True, channel=channel)
    if release is None:
        print(
            "kraft admin update: could not reach the release feed. Try again, "
            "or install by hand from the releases page.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    here = update.installed()
    if not update.is_behind(release) and not ns.force:
        # Name the channel unless it is the stable one an install on a final
        # release follows anyway, so a different channel never reads as the default.
        named = channel != "stable" or channel != update.channel_of(here)
        newest = "the newest release" + (f" on the {channel} channel" if named else "")
        print(f"kraft {here} is up to date ({release.tag} is {newest})")
        return
    if ns.restart:
        # Before installing, so answering no leaves nothing half done.
        _confirm_running_agents(ns, "restarting", ask=True, declined="nothing installed or stopped")
    print(f"kraft {here} -> {release.tag}")
    _confirm_dropped_plugins(release.tag.removeprefix("v"), ns.yes)
    code = update.perform(release)
    if code != 0:
        raise SystemExit(code)
    print(f"kraft {release.tag} installed.")
    if other := update.shadowing_kraft():
        print(
            f"warning: `kraft` on PATH is {other}, another install -- MCP servers and "
            "hooks keep running it; uninstall it or reorder PATH (kraft admin doctor)"
        )
    if ns.restart:
        _restart(ns)
    else:
        print("Restart a running server: kraft admin restart")


def _add_admin(subs, common: argparse.ArgumentParser) -> None:
    """This machine's server and its install. `start` is what bare `kraft` runs."""
    start = subs.add_parser("start", help="run the server (the same as bare `kraft`)")
    start.add_argument("--host", help="bind address (default: access.yaml, or KRAFT_HOST)")
    start.add_argument("--port", type=int, help="port (default: access.yaml, or KRAFT_PORT)")
    start.add_argument(
        "--detach",
        "-d",
        action="store_true",
        help="fork into its own session and return once it's up, instead of blocking this shell",
    )
    start.set_defaults(func=_cmd_start)

    stop = subs.add_parser(
        "stop",
        help="stop the running server; any agent it is running stops with it",
        description=(
            "Stop the running server. Any agent it is running stops with it, and each "
            "of those items stops when Kraft starts again. Pause running items first "
            "(kraft item pause ID) and resume them after. This lists the active items, "
            "but does not ask."
        ),
    )
    stop.set_defaults(func=_cmd_stop)

    restart = subs.add_parser(
        "restart",
        help=(
            "stop then start again, the same way it was running; this ends any running "
            "agent, so pause running items first and retry any it stopped"
        ),
        description=(
            "Stop then start again, the same way it was running. This ends any running "
            "agent, and each of those items stops when Kraft starts again: pause running "
            "items first (kraft item pause ID) and resume them after, or retry them. It "
            "lists the active items first and, in a terminal, asks before going on. With "
            "no server running it starts nothing and exits 1 (start one with kraft admin "
            "start). A server running attached to a terminal is stopped and not started "
            "again, since only that terminal can bring it back, and restart exits 1."
        ),
    )
    restart.add_argument("-y", "--yes", action="store_true", help="do not ask about running agents")
    restart.set_defaults(func=_cmd_restart)

    install_service = subs.add_parser(
        "install-service", help="write and load an OS service unit (KeepAlive / Restart=always)"
    )
    install_service.set_defaults(func=_cmd_install_service)

    uninstall_service = subs.add_parser("uninstall-service", help="remove the OS service unit")
    uninstall_service.set_defaults(func=_cmd_uninstall_service)

    health = subs.add_parser("health", parents=[common], help="the server's own status")
    health.set_defaults(func=_cmd_health)

    doctor_p = subs.add_parser(
        "doctor", parents=[common], help="check the whole install, one line per check"
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    from kraft.update import CHANNELS

    update_p = subs.add_parser("update", help="install the newest released kraft")
    update_p.add_argument("--force", action="store_true", help="install even when already current")
    update_p.add_argument(
        "--channel",
        choices=list(CHANNELS),
        default=None,
        help=(
            "stable, or a pre-release channel: rc, beta (beta and rc), alpha (any). "
            "Default: the channel of the version you have installed"
        ),
    )
    update_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "accept replacing a Kraft 0.x template configuration (registry.yaml, "
            "no library.yaml), which is backed up first; with --restart, do not ask "
            "about running agents"
        ),
    )
    update_p.add_argument(
        "--restart",
        action="store_true",
        help=(
            "restart a running server after a successful update, the same way it was "
            "running; this ends any running agent, so pause running items first"
        ),
    )
    update_p.set_defaults(func=_cmd_update)

    reindex = subs.add_parser("reindex", parents=[common], help="rescan documents into the index")
    reindex.add_argument("--repo", help="one repo path (default: all)")
    reindex.set_defaults(func=_cmd_reindex)

    reload_p = subs.add_parser(
        "reload",
        parents=[common],
        help="reread the template library, policy.yaml and intake.yaml from disk, no restart",
    )
    reload_p.set_defaults(func=_cmd_reload)

    templates.add(subs, common)
    plugin.add(subs, common)

    init = subs.add_parser(
        "init", parents=[common], help="register Kraft's MCP server and skills with an agent"
    )
    init.add_argument("--repo", action="store_true", help="install into this repo, not the user")
    init.set_defaults(func=_cmd_init)

    mcp = subs.add_parser("mcp", help="serve the MCP tools over stdio")
    mcp.set_defaults(func=_cmd_mcp)

    hook = subs.add_parser(
        "permission-hook",
        help=(
            "answer a harness's pre-tool hook from Kraft's permission gate "
            "(run by the agent's CLI, not by hand)"
        ),
    )
    hook.add_argument("harness", choices=sorted(permission_hooks.TRANSLATORS))
    hook.add_argument(
        "--fail-closed",
        action="store_true",
        help="deny, not no opinion, when Kraft cannot answer (also KRAFT_PERMISSION_FAIL_CLOSED=1)",
    )
    hook.set_defaults(func=_cmd_permission_hook)
