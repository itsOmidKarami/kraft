"""Opening a merge request, reading its CI, merging it.

One `Forge` shape, several backends, chosen by name in `registry.yaml`. A
direct-API backend is deliberately absent: `glab` and `gh` already hold their
credentials in the OS keyring, and a backend that talked to the REST API itself
would make Kraft responsible for a token — where it is read from, and that it
never reaches a log, an event payload, or a worker session's environment. That
is real work with no consumer until something runs without a CLI available
(Kraft-rki, Kraft-gzp).
"""

from __future__ import annotations

from kraft.adapters.forge.ci import *  # noqa: F403
from kraft.adapters.forge.gh import *  # noqa: F403
from kraft.adapters.forge.git import *  # noqa: F403
from kraft.adapters.forge.glab import *  # noqa: F403
from kraft.adapters.forge.models import *  # noqa: F403
from kraft.adapters.forge.mr import *  # noqa: F403
from kraft.adapters.forge.run import *  # noqa: F403
