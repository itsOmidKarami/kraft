"""What is saved but not yet running (W13 H): the restart items (`access.yaml`'s
bind and port against what the server is bound to), the reload items (a
templates-dir file whose bytes differ from the ones last loaded), the one
reload that clears the second kind, and the door that restarts a managed server.

Hashes are of bytes: a rewrite with the same bytes is no change. A load records
its file's hash first and loads second, so a write that lands in between reads
as pending rather than as loaded."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft import storage
from kraft.api import config_check, deps

logger = logging.getLogger(__name__)

WATCH_INTERVAL_S = 5
#: What `kraft admin plugin` writes; a reload of only these touches nothing else.
PLUGIN_FILES = ("plugins.yaml", "plugins.lock")
CONFIG_FILES = ("library.yaml", "policy.yaml", "intake.yaml", *PLUGIN_FILES)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _names(st) -> list[str]:
    """Every file a load reads: the fixed ones, and each chain on disk or loaded."""
    chains = {
        p.relative_to(st.templates_dir).as_posix() for p in st.templates_dir.glob("chains/*.yaml")
    }
    chains |= {n for n in getattr(st, "loaded_hashes", {}) if n.startswith("chains/")}
    return [*CONFIG_FILES, *sorted(chains)]


def set_loaded(st, name: str, digest: str | None) -> None:
    if not hasattr(st, "loaded_hashes"):
        st.loaded_hashes = {}
    st.loaded_hashes[name] = digest


def record(st, *names: str) -> None:
    """Take `names` (every watched file when none) as what is loaded now."""
    for name in names or _names(st):
        set_loaded(st, name, _digest(st.templates_dir / name))


def record_library(st, *, plugin_files: bool = True) -> None:
    """The library file, the plugin files it is built with (unless
    `plugin_files` is False: they were refused) and every chain file, and none
    that has since gone."""
    for name in [n for n in getattr(st, "loaded_hashes", {}) if n.startswith("chains/")]:
        del st.loaded_hashes[name]
    record(
        st,
        "library.yaml",
        *(PLUGIN_FILES if plugin_files else ()),
        *(n for n in _names(st) if n.startswith("chains/")),
    )


def _restart_items(st) -> list[dict]:
    try:
        access = config_mod.Access.load(st.templates_dir / "access.yaml")
    except config_mod.ConfigError:
        return []  # `config_check` of access.yaml says why
    bound = {"bind": st.bound_host, "port": st.bound_port}
    out = []
    for key, env in (("bind", "KRAFT_HOST"), ("port", "KRAFT_PORT")):
        # An override in the environment wins over the file: a restart would change nothing.
        if os.environ.get(env) or getattr(access, key) == bound[key]:
            continue
        out.append(
            {
                "id": f"access.{key}",
                "file": "access.yaml",
                "text": f"{key} changes from {bound[key]} to {getattr(access, key)}",
            }
        )
    return out


def _reload_items(st) -> list[dict]:
    loaded = getattr(st, "loaded_hashes", {})
    ctx = None
    out = []
    for name in _names(st):
        now = _digest(st.templates_dir / name)
        was = loaded.get(name)
        if now == was:
            continue
        item = {
            "id": f"disk:{name}",
            "file": name,
            "text": f"{name} "
            + ("was removed" if now is None else "was added" if was is None else "changed")
            + " on disk since it was loaded",
        }
        if now is not None:
            ctx = ctx or config_check.context(st)
            text = (st.templates_dir / name).read_text(errors="replace")
            if issues := config_check.check(name, text, ctx):
                item["problem"] = issues[0].message
        out.append(item)
    return out


def pending(st) -> dict:
    return {"restart": _restart_items(st), "reload": _reload_items(st), "managed": managed()}


def notify(app) -> dict:
    """The pending state; `apply_changed` goes out when its ids differ from the
    last answer. Only a live client is sent it."""
    st = app.state
    now = pending(st)
    signature = tuple(i["id"] for k in ("restart", "reload") for i in now[k])
    if signature != getattr(st, "apply_signature", ()):
        st.apply_signature = signature
        if bc := getattr(st, "broadcaster", None):
            bc.publish(
                "apply_changed", {"restart": len(now["restart"]), "reload": len(now["reload"])}
            )
    return now


async def watcher(app) -> None:
    """Rechecks the files, for hand edits no route sees."""
    while True:
        await asyncio.sleep(WATCH_INTERVAL_S)
        try:
            notify(app)
        except Exception:  # noqa: BLE001 -- a bad read must not end the watch
            logger.exception("apply check failed")


async def reload(app, only: Sequence[str] | None = None) -> str | None:
    """`policy.yaml`, the library and `intake.yaml` reread into the running server,
    the poller replaced. A policy that does not validate is refused and the
    running one kept (its reason is returned); so is an `intake.yaml` that
    does not load, its reason kept as `st.invalid_intake` for the caller,
    `/health` and doctor. Either stays pending.

    `only` the plugin files rebuilds the library from the lock and leaves a
    pending hand edit of `policy.yaml` or `intake.yaml` pending: a plugin verb
    applies its own change, not the operator's."""
    st = app.state
    if only is not None and set(only) <= set(PLUGIN_FILES):
        deps._reload_templates(st)
        notify(app)
        deps.in_background(app, deps.restore_plugins)
        return None
    refused = deps.reload_policy(st)
    # A limit added by this reload is judged against a stale measurement until
    # the next tick: measure now, without holding the reload.
    storage.kick(app)
    deps._reload_templates(st)
    digest = _digest(st.templates_dir / "intake.yaml")
    try:
        st.intake = config_mod.Intake.load(st.templates_dir / "intake.yaml").model_dump()
    except config_mod.ConfigError as exc:
        st.invalid_intake = str(exc)
    else:
        st.invalid_intake = None
        st.intake_off = False
        set_loaded(st, "intake.yaml", digest)
    await intake_mod.restart(app)
    notify(app)
    # A lock pulled into config/ may name a store this machine does not have.
    # In the background: a restore may fetch for longer than a caller waits.
    deps.in_background(app, deps.restore_plugins)
    return refused


def managed() -> bool:
    """Started by the service manager `kraft admin install-service` set up, whose
    names are the ones it writes."""
    from kraft.cli import admin

    if sys.platform == "darwin":
        return os.environ.get("XPC_SERVICE_NAME") == admin._LAUNCHD_LABEL
    if sys.platform.startswith("linux"):
        return "INVOCATION_ID" in os.environ and admin._service_installed()
    return False


def spawn_restart(st) -> None:
    """A detached `kraft admin restart`: this process cannot restart itself
    inside the request that asked."""
    from kraft.cli import admin

    with (st.run_dirs.logs / "server.log").open("ab") as log:
        subprocess.Popen(
            [admin._kraft_executable(), "admin", "restart"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
