"""Which `KRAFT_*` variables `conftest._isolated_kraft_home` deletes per test."""

from __future__ import annotations

#: The `KRAFT_*` read once, at collection or import, which a per-test scrub
#: cannot touch and must not hide: `pytest_collection_modifyitems` reads the
#: first two, `support.fake_beads` and the `fake_beads` fixture the third.
SUITE_SWITCHES = frozenset({"KRAFT_E2E", "KRAFT_E2E_REQUIRE", "KRAFT_TEST_REAL_BD"})

#: `KRAFT_E2E_GH_REPO`, `KRAFT_E2E_GLAB_REPO` and the rest: read-only inputs
#: that name a scratch forge for the real-CLI contract tests. Kraft itself never
#: reads one, so they cannot leak into a test; scrubbed, those tests skip, and a
#: skip reads as a pass.
SUITE_SWITCH_PREFIX = "KRAFT_E2E_"


def kraft_names_to_scrub(environ) -> list[str]:
    """The `KRAFT_*` names `_isolated_kraft_home` deletes from `environ`: all of
    them but the suite's own switches and the `KRAFT_E2E_*` scratch-forge inputs."""
    return [
        name
        for name in environ
        if name.startswith("KRAFT_")
        and name not in SUITE_SWITCHES
        and not name.startswith(SUITE_SWITCH_PREFIX)
    ]
