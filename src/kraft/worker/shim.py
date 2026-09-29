"""The `kraft` shim a sandboxed worker's callbacks go through (sandbox part
2, P5; spec §5): a POSIX `sh` + `curl` script shipped with Kraft, so an
image need not contain Kraft. It speaks only to the worker API, at
`callback.address("docker")`.

Mounted read-only, never taken from the image: a hooked harness whose hook
command is missing runs the call (codex treats an unrunnable hook as allow).
"""

from __future__ import annotations

from pathlib import Path

#: The installed directory holding the shim, and nothing else.
HOST_DIR = Path(__file__).parent / "bin"
#: Where every sandbox sees that directory, first on its PATH. Fixed, never
#: derived from a host path (spec, "Staying cloud-ready" rule 5).
CONTAINER_DIR = "/opt/kraft/bin"
