"""Opening a merge request, reading its CI, merging it.

One `Forge` shape, several backends, chosen per repo from the `forge` its
`repos.yaml` entry records (`run.backend_for`) and, for a `client: api` account, from the
backend its item pinned. `glab` and `gh` hold their own credentials. `gitlab-api` talks to
GitLab's REST API with the account's token (`credentials`, `http`); `github-api` is
the same over GitHub's REST and GraphQL APIs.
"""

from __future__ import annotations

from kraft.adapters.forge.ci import *  # noqa: F403
from kraft.adapters.forge.gh import *  # noqa: F403
from kraft.adapters.forge.git import *  # noqa: F403
from kraft.adapters.forge.github_api import GitHubApi  # noqa: F401
from kraft.adapters.forge.gitlab_api import *  # noqa: F403
from kraft.adapters.forge.glab import *  # noqa: F403
from kraft.adapters.forge.models import *  # noqa: F403
from kraft.adapters.forge.mr import *  # noqa: F403
from kraft.adapters.forge.run import *  # noqa: F403
