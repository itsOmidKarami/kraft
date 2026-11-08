"""The one place that knows how to talk to a local Kraft server.

`kraft admin mcp` and the `kraft` subcommands are both dispatch tables over this
package; neither holds logic the other lacks. Validation is not duplicated here —
it lives in `kraft.api`, where the UI already exercises it.
"""

from __future__ import annotations

from kraft.client.actions import *  # noqa: F403
from kraft.client.context import *  # noqa: F403
from kraft.client.reads import *  # noqa: F403
from kraft.client.transport import *  # noqa: F403

# `transport.remote` (the console's target) is a public module global, so the
# star-import above copied it in as None, and `from kraft.client import remote`
# would return that None instead of the submodule. Drop the copy: callers ask
# `client.is_remote()` or read `transport.remote`.
del remote  # noqa: F821
