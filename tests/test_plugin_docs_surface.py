"""Plugin docs shipped to the marketplace must not hardcode GitLab URLs.

Kraft's plugin docs reach marketplace users via `/plugin install kraft@kraft`.
After the GitHub migration (Tasks 11-16), gitlab.com URLs will point at a
private, unreachable repo. These files need to ship with GitHub URLs instead.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DOCS = (
    "plugins/kraft/README.md",
    "plugins/kraft/skills/board/SKILL.md",
    "plugins/kraft/skills/check/SKILL.md",
    "plugins/kraft/skills/gates/SKILL.md",
    "plugins/kraft/skills/handoff/SKILL.md",
    "plugins/kraft/skills/onboard/SKILL.md",
    "plugins/kraft/skills/status/SKILL.md",
    "plugins/kraft-lite/README.md",
    "plugins/kraft-lite/skills/gate/SKILL.md",
    "plugins/kraft-lite/skills/init/SKILL.md",
    "plugins/kraft-lite/skills/next/SKILL.md",
    "plugins/kraft-lite/skills/start/SKILL.md",
    "plugins/kraft-lite/skills/status/SKILL.md",
)


def test_plugin_docs_surface_points_at_github():
    for name in PLUGIN_DOCS:
        text = (ROOT / name).read_text()
        assert "gitlab.com" not in text, f"{name} still points at GitLab"
