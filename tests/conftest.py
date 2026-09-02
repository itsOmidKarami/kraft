from __future__ import annotations

import os
import shutil

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("KRAFT_E2E") == "1" and shutil.which("claude"):
        return
    skip = pytest.mark.skip(reason="e2e: set KRAFT_E2E=1 and install `claude` to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)
