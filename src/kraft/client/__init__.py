"""The one place that knows how to talk to a local Kraft server.

`kraft admin mcp` and the `kraft` subcommands are both dispatch tables over this
package; neither holds logic the other lacks. Validation is not duplicated here —
it lives in `kraft.api`, where the UI already exercises it.
"""

from __future__ import annotations

# After the star-imports: transport has a module global named `remote` (the
# console's target), which the star-import copies in as None, and a later
# `from kraft.client import remote` would return that None. Importing the
# submodule by its dotted name replaces the attribute with the module.
import kraft.client.remote  # noqa: E402, F401, I001
from kraft.client.actions import *  # noqa: F403
from kraft.client.context import *  # noqa: F403
from kraft.client.reads import *  # noqa: F403
from kraft.client.transport import *  # noqa: F403
