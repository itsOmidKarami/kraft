from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def kraft_home() -> Path:
    """The directory an installed Kraft keeps everything in.

    One home, not a path guessed from `__file__`: the source tree is the install
    only while Kraft runs from a checkout, and under site-packages counting
    parent directories lands somewhere meaningless. `KRAFT_HOME` moves it, which
    is how `just dev` gets an instance that cannot touch the real one.
    """
    return Path(os.environ.get("KRAFT_HOME") or Path.home() / ".kraft").expanduser()


def private(path: str, flags: int) -> int:
    """An `opener=` for `open()` that creates the file 0600: a log under the
    run dir holds a whole agent session."""
    return os.open(path, flags, 0o600)


def default_run_dir() -> Path:
    return kraft_home() / "run"


def default_config_dir() -> Path:
    """Where an install keeps its configuration: the policy, the connected
    repositories, the harness profiles, access, and the templates (the library
    and the chains) an item is filed from. `config/` since 2.0; a home still
    holding the 1.x `templates/` is renamed once at start, leaving `templates`
    as a link to it (`cli.admin.adopt_pre_2_home`)."""
    return kraft_home() / "config"


#: The 1.x name of `default_config_dir()`. Read when the current one is unset,
#: so a shell, a service unit or a Kit built for 1.x keeps pointing at the same
#: directory; written nowhere.
LEGACY_CONFIG_DIR_VAR = "KRAFT_TEMPLATES_DIR"


def config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """The config directory this process runs against: `KRAFT_CONFIG_DIR`, else
    the 1.x `KRAFT_TEMPLATES_DIR`, else `default_config_dir()`. Read at call
    time, never at import, so a test or a reload that sets the variable after
    import still takes effect."""
    env = os.environ if environ is None else environ
    named = env.get("KRAFT_CONFIG_DIR") or env.get(LEGACY_CONFIG_DIR_VAR)
    if named:
        return Path(named)
    default = default_config_dir()
    legacy = pre_2_config_dir()
    if not default.exists() and legacy is not None and legacy != default:
        # Until the first 2.0 start renames it (`cli.admin.adopt_pre_2_home`),
        # a 1.x home's files are where they were: `kraft admin doctor` or
        # `kraft view list` run before that restart must read them, not
        # report an empty home beside them.
        return legacy
    return default


def pre_2_config_dir() -> Path | None:
    """`$KRAFT_HOME/templates`, when it holds a 1.x home (a `library.yaml`)
    or a 0.x one (a `registry.yaml`): what `default_config_dir()` was called
    before 2.0. None when there is no such directory."""
    old = kraft_home() / "templates"
    if (old / "library.yaml").is_file() or (old / "registry.yaml").is_file():
        return old
    return None


def default_skills_dir() -> Path:
    """Where an operator may override a bundled method file.

    Not seeded by `cli.seed_home` and usually absent: the shipped skills live in
    the package (`kraft.skill.BUNDLED`), and this directory exists only when
    someone has deliberately overridden one.
    """
    return kraft_home() / "skills"


def default_harnesses_dir() -> Path:
    """Where an operator may override or add a harness definition.

    Same shape and same reason as `default_skills_dir`: the shipped harnesses
    live in the package (`kraft.harness.BUNDLED`), and this directory exists
    only when someone has deliberately added or overridden one. Not seeded by
    `cli.seed_home` -- a seeded copy would freeze at whichever version the
    operator first installed, which is the drift measured live on 2026-09-13
    (Kraft-717xy).

    1.x kept it at `$KRAFT_HOME/templates/harnesses` whatever
    `KRAFT_TEMPLATES_DIR` named, so a home whose config lived elsewhere has
    it there, in a `templates/` the 2.0 rename never adopts: read until a
    `config/harnesses` exists.
    """
    current = default_config_dir() / "harnesses"
    legacy = kraft_home() / "templates" / "harnesses"
    if not current.is_dir() and legacy.is_dir():
        return legacy
    return current


#: Built SPA and default config, copied in by `just install`. Absent in a plain
#: source checkout — the API then serves no SPA, and `just dev` points
#: KRAFT_FRONTEND_DIST at vite's output instead.
BUNDLED = Path(__file__).parent / "_bundled"


@dataclass(frozen=True)
class RunDirs:
    base: Path

    @property
    def db(self) -> Path:
        return self.base / "orchestrator.db"

    @property
    def index_db(self) -> Path:
        return self.base / "index.db"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    @property
    def results(self) -> Path:
        return self.base / "results"

    @property
    def pid(self) -> Path:
        """The running server's pid, for `kraft admin stop`.

        Under the run dir rather than a fixed system path because it belongs to
        one instance: a `just dev` server and an installed one must be able to
        run at once, and they differ only by where this directory points.
        """
        return self.base / "kraft.pid"

    @property
    def mode(self) -> Path:
        """How the running server was started -- "detached" or "attached" --
        so `kraft admin restart` can start it back up the same way."""
        return self.base / "kraft.mode"

    @property
    def worktrees(self) -> Path:
        return self.base / "worktrees"

    @property
    def attachments(self) -> Path:
        """Intake attachments, copied here at intake rather than referenced in
        place (Kraft-eqgn).

        The gate an attachment satisfies is trimmed out of the chain at
        intake, and once the item starts it cannot be put back (before then,
        PATCH /work-items re-snapshots or drops it, Kraft-s7c04.28), so the
        document that justified the trim has to be one Kraft owns. Referencing a path in
        someone else's working tree meant a file deleted in between left the
        item running with its spec and plan gates gone and nothing said.
        """
        return self.base / "attachments"

    @property
    def sockets(self) -> Path:
        """Each sandboxed session's egress socket, `sn/<short>/s.sock`
        (`worker.channel`): kept short, since a unix socket path may be at
        most 104 bytes on macOS and 108 on Linux."""
        return self.base / "sn"

    @property
    def ca(self) -> Path:
        """The Kraft CA, its key (0600) and the certificates it signed
        (`worker.ca`); the egress TLS listener's persisted port."""
        return self.base / "ca"

    @property
    def kit(self) -> Path:
        """Each Kit's descriptor as fetched, `<digest hex>.json`
        (`worker.kit`): keyed by the digest a policy pins, so never stale."""
        return self.base / "kit"

    def ensure(self) -> RunDirs:
        """Also makes the run dir private (Kraft-9efnk.18): it holds whole
        agent sessions. The chmod tightens an install made before this, and
        only the directory itself, so nothing under it is walked. Worktrees
        stay reachable: every sandbox runs as the operator's own uid
        (`docker.Host.user_args`)."""
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base.chmod(0o700)
        for d in (self.logs, self.results, self.worktrees, self.attachments):
            d.mkdir(parents=True, exist_ok=True)
        return self
