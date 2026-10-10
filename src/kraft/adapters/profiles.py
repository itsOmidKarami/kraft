"""Which harness profile and agent profile a task launches on, read live from
`harnesses.yaml` at every launch (`unavailable-selected-harness-needs-human`,
Kraft-ps1ao). Re-exported by `kraft.adapters.agent`, whose names these are."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kraft import harness as _harness
from kraft.paths import config_dir
from kraft.plugins import load as plugins_load
from kraft.templates.environment import (
    HarnessProfile,
    HarnessProfileTable,
    TemplateEnvironmentError,
)


class HarnessUnavailable(Exception):
    """A task's `harness:` names no enabled profile this instance can launch
    (`unavailable-selected-harness-needs-human`). The message says why."""


class ProfileUnavailable(HarnessUnavailable):
    """A task's `profile:` names no agent profile, or one that cannot run on
    its harness (Kraft-ps1ao). Stops for a human; nothing is substituted."""


#: The profile defaults `resolve_agent_task` applies. Each is a scalar option
#: `run_agent_task` takes; a default outside this set would be dropped without
#: a word, so it is refused instead.
_PROFILE_DEFAULTS = ("model", "effort", "permission_mode")


def harness_table(
    harnesses: _harness.HarnessSet, pins: Mapping[str, Mapping[str, str | None]] | None = None
) -> tuple[HarnessProfileTable, Path]:
    """The live `harnesses.yaml` and its path, or `HarnessUnavailable`. Read
    from the app's templates directory (`KRAFT_CONFIG_DIR`, else
    `$KRAFT_HOME/config`) on every call, so an edit reaches the next launch.
    A plugin's agent profiles are the exception: `pins` are the versions a
    work item started with (`ResolvedChain.plugins`), and theirs are read."""
    path = config_dir() / "harnesses.yaml"
    try:
        table = HarnessProfileTable.from_yaml(
            path, harnesses=harnesses.valid, plugins=plugins_load.for_item(pins, path.parent)
        )
        return table, path
    except TemplateEnvironmentError as exc:
        raise HarnessUnavailable(str(exc)) from exc


def harness_profile(profile_id: str, harnesses: _harness.HarnessSet) -> HarnessProfile:
    """The enabled `harnesses.yaml` profile `profile_id` names, read live
    (`harness_table`), or `HarnessUnavailable`. Never a fallback onto a
    provider of the same name -- a task selects a profile, and a missing one
    stops for a human."""
    table, path = harness_table(harnesses)
    return select_profile(table.profiles, profile_id, path)


def resolve_profile(
    name: str, harness: HarnessProfile, table: HarnessProfileTable, providers
) -> tuple[str, str | None]:
    """The (model, effort) agent profile `name` gives a task on `harness`, or
    `ProfileUnavailable` in `pairing_problem`'s words."""
    if why := table.pairing_problem(name, harness, providers):
        raise ProfileUnavailable(why)
    entry = table.agent_profiles[name].providers[harness.provider]
    return entry.model, entry.effort


def select_profile(profiles: dict[str, HarnessProfile], pid: str, path: Path) -> HarnessProfile:
    """`harness_profile` over a loaded table, which a save checks (Kraft-archr)."""
    profile = profiles.get(pid)
    if profile is None:
        raise HarnessUnavailable(f"{path} defines no such profile; known are {sorted(profiles)}")
    if not profile.is_available():
        raise HarnessUnavailable(f"profile {pid!r} is disabled in {path}")
    unapplied = sorted(set(profile.defaults) - set(_PROFILE_DEFAULTS))
    if unapplied:
        raise HarnessUnavailable(
            f"profile {pid!r} sets defaults {unapplied}, which Kraft does not "
            f"apply; only {list(_PROFILE_DEFAULTS)} are"
        )
    return profile
