from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from kraft import config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bind() -> tuple[str, int]:
    """Bind address from access.yaml — this is the "takes effect on restart" in
    Settings → Access (design 5e). Env still wins, for a one-off run."""
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or _REPO_ROOT / "templates")
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host not in config.LOOPBACK and not access["password_hash"]:
        raise SystemExit(
            f"refusing to bind {host}: no password is set. Set one in Settings → Access "
            "while running on 127.0.0.1, or add password_hash to access.yaml."
        )
    return host, port


if __name__ == "__main__":
    host, port = _bind()
    uvicorn.run("kraft.api:app", host=host, port=port, log_level="warning")
