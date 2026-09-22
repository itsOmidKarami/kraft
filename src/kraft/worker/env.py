"""A worker's environment, derived from the repo it works on.

`{**os.environ, ...}` gave every worker whatever shell started the daemon,
frozen at `kraft admin start` -- a bare launchd PATH decided which interpreter
won, and a daemon started in one repo exported that repo's variables to every
other repo's workers (Kraft-69atv). An allowlist is what makes the environment
a function of the repo instead: a name not listed is simply not there, so
`VIRTUAL_ENV`, `PYTHONPATH`, `UV_*` and `DIRENV_*` need no denylist to
maintain.
"""

import os
from typing import TYPE_CHECKING

from kraft.worker.sandbox import FORWARDED_ENV

if TYPE_CHECKING:
    from kraft.config import RepoEntry

#: What a host process needs to run at all, plus network reachability. Forge
#: credentials are deliberately absent: Kraft's own forge calls go through
#: `git.run_git`, and a worker does not push, merge or close beads. A repo
#: whose node genuinely needs one names it in `env_passthrough`.
#:
#: The `KRAFT_*` names below are a property of the install, not of a repo, so
#: they belong here rather than behind `env_passthrough`: `KRAFT_HOME`,
#: `KRAFT_RUN_DIR`, `KRAFT_TEMPLATES_DIR`, `KRAFT_SKILLS_DIR`, `KRAFT_HOST`
#: and `KRAFT_PORT` are what a worker's own `kraft` CLI and MCP client use to
#: find the instance that launched them (`paths.kraft_home`,
#: `transport.base_url`, `transport.http`) -- without them a worker started
#: by a non-default instance talks to `~/.kraft` on the default port instead.
#: `KRAFT_DAEMON_PID` and `KRAFT_DAEMON_PORT` are what
#: `adapters.agent.SAFETY_RULES` -- carried on every agent launch, not a
#: steering file -- tells a worker to check before killing anything it finds
#: listening on a port.
BASELINE = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "TERM",
        "TZ",
        "TMPDIR",
        "SSH_AUTH_SOCK",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "KRAFT_HOME",
        "KRAFT_RUN_DIR",
        "KRAFT_TEMPLATES_DIR",
        "KRAFT_SKILLS_DIR",
        "KRAFT_HOST",
        "KRAFT_PORT",
        "KRAFT_DAEMON_PID",
        "KRAFT_DAEMON_PORT",
    }
)


def worker_env(repo_entry: RepoEntry | None, extra: dict | None = None) -> dict[str, str]:
    """The environment for one worker process.

    Two steps that must not blur: copy a set of *names* out of `os.environ`,
    then overlay literal key-value pairs. A copied name absent from the
    daemon's environment is left unset rather than set empty.
    """
    passthrough = repo_entry.env_passthrough if repo_entry is not None else []
    names = BASELINE | set(FORWARDED_ENV) | set(passthrough)
    base = {k: os.environ[k] for k in names if k in os.environ}
    return base | (repo_entry.env if repo_entry is not None else {}) | (extra or {})
