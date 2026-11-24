"""The Herdr plugin's own test configuration. It runs with nothing installed
but pytest (CI's herdr-floor job), so it imports nothing from Kraft."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# The plugin's module, and this directory's `herdr_fakes`.
for directory in (PLUGIN, Path(__file__).resolve().parent):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
