"""Template-library fixtures: the smallest agent task, and a template
directory written from mappings and loaded back."""

from __future__ import annotations

from pathlib import Path

import yaml

from kraft.templates.library import CHAINS_DIR, LIBRARY_FILE, TemplateLibrary


def agent_task(**kw) -> dict:
    return {"kind": "agent", "harness": "codex", "prompt": "do it", **kw}


def write_chains(root: Path, library: dict, chains: dict[str, dict]) -> TemplateLibrary:
    """`library` and each of `chains` (by id) written under `root`, loaded back
    as the `TemplateLibrary` they make."""
    (root / CHAINS_DIR).mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_FILE).write_text(yaml.safe_dump(library))
    for id, chain in chains.items():
        (root / CHAINS_DIR / f"{id}.yaml").write_text(yaml.safe_dump(chain))
    return TemplateLibrary.from_yaml_dir(root)
