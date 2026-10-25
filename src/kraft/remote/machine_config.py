"""`machine.yaml`: what a machine says about itself, in the config directory of the Kraft that
runs its launches. Today only `capacity:`, the steps this machine runs at once however many a
server would send it."""

from __future__ import annotations

import logging
from pathlib import Path

from kraft import paths
from kraft.config import ConfigError, read_yaml

FILE = "machine.yaml"

logger = logging.getLogger(__name__)


def path() -> Path:
    return paths.config_dir() / FILE


def capacity(file: str | Path) -> int | None:
    """The `capacity:` in `file`; None for a missing file or key. Raises `ConfigError`."""
    value = read_yaml(file).get("capacity")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{FILE}: capacity must be a whole number of at least 1, not {value!r}")
    return value


def limit() -> int | None:
    """This machine's own limit, read fresh. A broken file is logged and reads as no limit: a
    machine must not stop taking work, or stop reporting, over a typo."""
    where = path()
    try:
        return capacity(where)
    except ConfigError as exc:
        logger.warning("%s (%s): ignored, no limit of its own", exc, where)
        return None
