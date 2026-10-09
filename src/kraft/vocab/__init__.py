"""The vocabularies Kraft owns, defined once (see the closed-sets spec).

A leaf package: standard library only, so `db.py` can import it.
"""

from __future__ import annotations

from kraft.vocab.display import DisplayStatus as DisplayStatus
from kraft.vocab.events import *  # noqa: F403
from kraft.vocab.review import *  # noqa: F403
from kraft.vocab.session import *  # noqa: F403
from kraft.vocab.stop import *  # noqa: F403
from kraft.vocab.work_item import *  # noqa: F403
