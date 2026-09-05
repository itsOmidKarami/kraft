"""The `kraft` command: seed a home if there isn't one, then serve.

`python -m kraft` and the installed console script are the same code path, so a
checkout and an install can only ever differ in where their paths point.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import uvicorn

from kraft import config
from kraft.paths import BUNDLED, default_templates_dir


def seed_home(templates_dir: Path) -> bool:
    """Copy the packaged config into an empty home. True if it seeded.

    Only ever creates. An upgrade must not overwrite a policy the operator
    edited, and `access.yaml` is never bundled — it holds a password hash and a
    bind address that belong to one machine. `notify.yaml` is never bundled
    either, for the same reason: it usually holds a webhook URL with a bearer
    token embedded, and that belongs to one machine too. Reachable today by
    anyone who points `KRAFT_TEMPLATES_DIR` at a checkout with a live
    notify.yaml and runs `just install` -- if either file ever slips into
    `BUNDLED / "templates"`, it must still not reach a seeded home.
    """
    if templates_dir.exists():
        return False
    if not (BUNDLED / "templates").is_dir():
        raise SystemExit(
            f"kraft: no config in {templates_dir} and no bundled defaults to seed it "
            "with. This build shipped without them — reinstall with `just install`, "
            "or point KRAFT_TEMPLATES_DIR at a config directory."
        )
    # Build beside the target and rename: an interrupted copy must not leave a
    # half-seeded home that every later start then treats as already seeded.
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(BUNDLED / "templates", staging)
    (staging / "access.yaml").unlink(missing_ok=True)
    (staging / "notify.yaml").unlink(missing_ok=True)
    staging.rename(templates_dir)
    return True


def _bind(templates_dir: Path) -> tuple[str, int]:
    """Bind address from access.yaml — this is the "takes effect on restart" in
    Settings → Access (design 5e). Env still wins, for a one-off run."""
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host not in config.LOOPBACK and not access["password_hash"]:
        raise SystemExit(
            f"refusing to bind {host}: no password is set. Set one in Settings → Access "
            "while running on 127.0.0.1, or add password_hash to access.yaml."
        )
    return host, port


def _serve() -> None:
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    host, port = _bind(templates_dir)
    print(f"kraft: http://{host}:{port}")
    uvicorn.run("kraft.api:app", host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    """Bare `kraft` serves, as it always has. Subcommands are the agent surface.

    argparse is deliberately not used: serving must stay the zero-argument
    default, and one string compare is the whole dispatch.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        _serve()
        return
    if args[0] == "mcp":
        from kraft.mcp import serve_stdio

        serve_stdio()
        return
    if args[0] == "init":
        from kraft.init import install

        for path in install(repo_scope="--repo" in args[1:]):
            print(f"kraft: wrote {path}")
        return
    raise SystemExit(
        f"kraft: unknown command {args[0]!r} (try `kraft`, `kraft mcp`, or `kraft init`)"
    )
