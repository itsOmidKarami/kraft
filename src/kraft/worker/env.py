"""A worker's environment, derived from the repo it works on.

`{**os.environ, ...}` gave every worker whatever shell started the daemon,
frozen at `kraft admin start` -- a bare launchd PATH decided which interpreter
won, and a daemon started in one repo exported that repo's variables to every
other repo's workers (Kraft-69atv). An allowlist is what makes the environment
a function of the repo instead: a name not listed is simply not there, so
`VIRTUAL_ENV`, `PYTHONPATH`, `UV_*` and `DIRENV_*` need no denylist to
maintain.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from kraft.worker.sandbox import FORWARDED_ENV

if TYPE_CHECKING:
    from kraft.config import RepoEntry

#: What a host process needs to run at all, plus network reachability: the
#: proxy variables, and the CA variables a host behind a TLS-intercepting proxy
#: sets for each tool that reads its own (a sandboxed worker gets neither as
#: is; `worker.backends.docker_forward` decides what crosses). Forge
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
#: `KRAFT_DAEMON_PID` and `KRAFT_DAEMON_PORT` are what the
#: `never-signal-processes-you-didnt-start` steering profile (opt-in, via a
#: repo's `repos.yaml` `steering:`) tells a worker to check before killing
#: anything it finds listening on a port.
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
        "ALL_PROXY",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
        "NODE_EXTRA_CA_CERTS",
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


#: What a test command runs with on top of `worker_env`. The fix loop re-runs
#: the tests after an agent edits source in the same worktree, and a .pyc
#: written on an earlier cycle has the same second-resolution mtime and
#: (often) size as the fixed source, so CPython would import the stale
#: bytecode and the re-measure would never see the fix. Never writing
#: bytecode keeps every cycle honest.
TEST_ENV = {"PYTHONDONTWRITEBYTECODE": "1"}


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
