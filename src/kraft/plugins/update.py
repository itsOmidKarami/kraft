"""Judging a candidate plugin before anything is written.

`check` is what a plugin's own files may not say, whatever instance takes it:
a plugin never runs a command, never grants a permission, runs only on the
harnesses it lists, and reaches into no other plugin. The rules read the
files as the author wrote them. A plugin is self-contained, so everything one
of its chains can inherit is in these same files: checking each file checks
every chain after `extends:` expansion, and the components no chain uses yet.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping

import yaml

from kraft.config import bounded_yaml
from kraft.plugins.manifest import PluginManifest

#: The `policy:` keys a plugin may set: how long, how much, how many times.
#: An allowlist, so a key a later Kraft adds is refused until it is listed.
LIMITS = frozenset(
    {
        "time_cap_minutes",
        "total_time_cap_minutes",
        "timeout_minutes",
        "max_attempts",
        "budget_usd",
        "token_budget",
        "deny_tools",
    }
)
#: Kraft's own qualifier: `kraft:spec` is a method Kraft ships.
_KRAFT = "kraft"
_SECTIONS = ("steering", "tasks", "steps", "nodes")


class Refused(Exception):
    """A plugin Kraft will not take. `problems` is every reason, each naming
    its file and key."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _mappings(data: object, at: str) -> Iterator[tuple[str, Mapping]]:
    """Every mapping under `data`, with the key path it sits at."""
    if isinstance(data, Mapping):
        yield at, data
        for key, value in data.items():
            yield from _mappings(value, f"{at}.{key}" if at else str(key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            label = value.get("id", index) if isinstance(value, Mapping) else index
            yield from _mappings(value, f"{at}[{label}]")


def _components(rel: str, data: object) -> Iterator[tuple[str, Mapping]]:
    """The authored components of one file. A `library.yaml` section maps
    names to definitions, so its keys are names, never fields: a task named
    `policy` is not a policy block."""
    if not isinstance(data, Mapping):
        return
    if rel == "library.yaml":
        for section in _SECTIONS:
            entries = data.get(section)
            for name, body in entries.items() if isinstance(entries, Mapping) else ():
                yield from _mappings(body, f"{section}.{name}")
    else:
        yield from _mappings(data, "")


def _qualifier(value: object) -> str | None:
    if isinstance(value, str) and ":" in value:
        return value.split(":", 1)[0]
    return None


def problems(
    found: PluginManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
) -> list[str]:
    """Every reason this plugin's content is refused; empty when it is taken.
    `other_namespaces` are the installed plugins' namespaces, this one's own
    left out: a skill reference into one of them is a plugin dependency."""
    out: list[str] = []
    allowed_harnesses = set(found.requires.harnesses)
    for rel in sorted(files):
        if not (rel == "library.yaml" or rel.startswith("chains/")):
            continue
        try:
            data = bounded_yaml(files[rel][1].decode(), [20_000])
        except (ValueError, yaml.YAMLError) as exc:
            out.append(f"{rel}: cannot parse: {exc}")
            continue
        for at, component in _components(rel, data):
            where = f"{rel}: {at}" if at else rel
            if component.get("kind") == "subprocess":
                out.append(
                    f"{where}: a plugin may not carry a subprocess task; a shell command runs "
                    "outside the permission gate an agent works under"
                )
            policy = component.get("policy")
            for key in policy if isinstance(policy, Mapping) else ():
                if key not in LIMITS:
                    out.append(
                        f"{where}: policy.{key} is not a limit; a plugin may set only "
                        f"{', '.join(sorted(LIMITS))}. Permissions stay in the instance's "
                        "policy.yaml"
                    )
            harness = component.get("harness")
            if isinstance(harness, str) and harness not in allowed_harnesses:
                out.append(
                    f"{where}: harness {harness!r} is not listed in requires.harnesses "
                    f"({sorted(allowed_harnesses)})"
                )
            steering = component.get("steering")
            references = [
                ("extends", component.get("extends")),
                ("profile", component.get("profile")),
                *(("steering", name) for name in (steering if isinstance(steering, list) else ())),
            ]
            for key, value in references:
                qualifier = _qualifier(value)
                if qualifier is not None and qualifier != found.name:
                    out.append(
                        f"{where}: {key} {value!r} reaches outside the plugin; a plugin is "
                        "self-contained"
                    )
            skill = component.get("skill")
            if _qualifier(skill) in set(other_namespaces) - {found.name, _KRAFT}:
                out.append(
                    f"{where}: skill {skill!r} names another installed Kraft plugin; there are "
                    "no plugin dependencies"
                )
    return out


def check(
    found: PluginManifest,
    files: Mapping[str, tuple[str, bytes]],
    *,
    other_namespaces: Collection[str] = (),
) -> None:
    """Raise `Refused` when this plugin's content may not be installed."""
    if found_problems := problems(found, files, other_namespaces=other_namespaces):
        raise Refused(found_problems)
