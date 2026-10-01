"""Registers the config-file draft areas with `store.AREAS` and
`resolve._AREAS`. Done on first use, not at import: `resolve` imports the API
package, which imports the drafts routes, so a module-level registration would
run while `resolve` is half-loaded."""

from __future__ import annotations

from kraft.drafts import harnesses, intake, policy, repos, resolve, store


def register() -> None:
    for area in (harnesses, repos, policy, intake):
        store.AREAS[area.NAME] = area.AREA
        resolve._AREAS[area.NAME] = area.resolve
