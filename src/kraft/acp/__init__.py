"""ACP agents: the entry, the client Kraft runs as the session's command, the log it writes."""

from __future__ import annotations

_cache: tuple[tuple, frozenset[str]] | None = None


def _signature() -> tuple:
    """The harness files' names, sizes and mtimes: what `harness.load` would read."""
    from kraft import harness
    from kraft.paths import default_harnesses_dir

    files = []
    for directory in (default_harnesses_dir(), harness.BUNDLED):
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                st = path.stat()
                files.append((str(path), st.st_size, st.st_mtime_ns))
    return tuple(files)


def harness_ids(harnesses=None) -> frozenset[str]:
    """Ids of the `kind: acp` harnesses (a session row records only the harness id,
    `worker_sessions.harness`). Given a loaded `HarnessSet` it reads that; otherwise it
    loads once per change of the harness files, not once per call: the spend checks
    ask on every launch and every session exit."""
    global _cache
    from kraft import harness

    def ids(hs) -> frozenset[str]:
        return frozenset(i for i, h in hs.valid.items() if h.kind == "acp")

    if harnesses is not None:
        return ids(harnesses)
    sig = _signature()
    if _cache is None or _cache[0] != sig:
        _cache = (sig, ids(harness.load(None)))
    return _cache[1]
