#!/usr/bin/env python
"""Write the config JSON Schemas the VS Code extension bundles. `just schemas`."""

from __future__ import annotations

from pathlib import Path

from kraft import config_schemas

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in config_schemas.write_all(root / config_schemas.SCHEMA_DIR):
        print(path.relative_to(root))
