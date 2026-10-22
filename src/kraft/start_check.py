"""`python -m kraft.start_check`: can the Kraft installed now start.

Run by the update loop, with the tool's own interpreter, on a freshly installed
version before the running server is stopped. It imports the server, reads
every configuration file through its model, and checks the bind address and
port the server would listen on. It does not bind them and writes nothing.
Exit 0, or 1 with the reason on stderr.
"""

from __future__ import annotations

import ipaddress
import re
import sys

_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


def problem() -> str | None:
    """Why this install would not start, or None."""
    from kraft import config
    from kraft.api import app  # noqa: F401 -- the server imports whole, as it will
    from kraft.cli import admin
    from kraft.paths import config_dir

    directory = config_dir()
    try:
        for model in config._Model.__subclasses__():
            if file := getattr(model, "FILE", None):  # a file's model; the rest are its parts
                model.load(directory / file)
        host, port = admin._bind(directory)
    except config.ConfigError as exc:
        return str(exc)
    except SystemExit as exc:
        return str(exc.code)
    if not 0 <= port <= 65535:
        return f"access.yaml: port {port} is not a port"
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            return f"access.yaml: bind {host!r} is not an address"
    return None


def main() -> int:
    try:
        why = problem()
    except Exception as exc:  # noqa: BLE001 -- anything that stops it starting is the answer
        why = f"{type(exc).__name__}: {exc}"
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
