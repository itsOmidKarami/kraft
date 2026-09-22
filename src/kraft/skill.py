"""The *method* an agent hook follows, injected through the system prompt.

Sibling of `steering.py`, and deliberately shaped like it: same bare-name rule,
same validate/read split, same reason for the split — the value must round-trip
through `GET /registry` and the Settings screens as a name, so resolving it into
the config dict would inline a method body into the operator's YAML.

Where it differs is the fallback. Steering is operator-authored and exists only
under `$KRAFT_HOME`; a method is Kraft-authored and *shipped*, with the operator
directory as an optional overlay on top. A value containing `:` is not a file
at all: it names a skill in some other tool's plugin system, which Kraft cannot
read, so the agent is told to load it by name. The one exception is Kraft's own
qualifier: `kraft:spec` is the method Kraft ships as `spec`.
"""

from __future__ import annotations

from pathlib import Path

#: Method files that ship with Kraft. Read from the package, never from
#: `$KRAFT_HOME/templates/`, because `cli.seed_home` copies templates once and
#: never again — a shipped method would then be frozen at whichever version the
#: operator first installed.
BUNDLED = Path(__file__).parent / "skills"

#: What `adapters/agent.py` wraps the method text in.
HEADING = "\n\n## Method\n\n"

#: Appended after the method text, whatever its source. A plugin reference can
#: name a skill this agent's installation does not have; without this the agent
#: improvises a method and the human finds out at the gate.
UNAVAILABLE = (
    "\n\nIf you cannot load the method named above, stop with status "
    '"needs_context" and say in "question" exactly which method was missing. Do '
    "not improvise a method of your own."
)

#: What a `provider:name` value becomes. Kraft cannot read another tool's plugin
#: cache, so the reference is handed to the agent, which can.
PLUGIN_PROMPT = "Follow the {ref} skill for how to do this work. Load it before you start."


#: The plugin Kraft itself is. A V1 skill name is plugin-qualified
#: (`templates.models.AgentTask.skill`), and `kraft:spec` names Kraft's own
#: shipped method -- the same file a bare `spec` does (Kraft-vhcop).
OWN_PLUGIN = "kraft:"


class SkillError(Exception):
    pass


#: Shipped methods that were renamed, old name to new. `library.yaml` is seeded
#: once and never overwritten, so a home seeded before the rename still names
#: the old one (Kraft-35u4m.1).
RENAMED = {"mr-checks-repair": "mr-metadata-repair"}


def _own_name(value: str) -> str:
    """`value` with Kraft's own plugin qualifier removed, if it has one, and a
    renamed method's old name read as its new one."""
    name = value.removeprefix(OWN_PLUGIN)
    return RENAMED.get(name, name)


def is_plugin_ref(value: str) -> bool:
    """A `:` means "somebody else's skill system", not a file name -- except
    Kraft's own `kraft:` qualifier, which names a method Kraft ships."""
    return not value.startswith(OWN_PLUGIN) and ":" in value


def _local_path(skills_dir: Path | None, name: str, where: str) -> Path:
    if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
        raise SkillError(
            f"{where}: skill name {name!r} must be a bare directory name — Kraft "
            "never reads a method file from outside its own skills directories"
        )
    if skills_dir is not None:
        overlay = Path(skills_dir) / name / "SKILL.md"
        if overlay.is_file():
            return overlay
    bundled = BUNDLED / name / "SKILL.md"
    if bundled.is_file():
        return bundled
    looked = [str(bundled)]
    if skills_dir is not None:
        looked.insert(0, str(Path(skills_dir) / name / "SKILL.md"))
    raise SkillError(f"{where}: skill {name!r} not found at {' or '.join(looked)}")


def path_for(skills_dir: Path | None, name: str, *, where: str) -> Path:
    """The file a bare or `kraft:` skill name resolves to, or raise. Not for
    another plugin's refs."""
    return _local_path(skills_dir, _own_name(name), where)


def validate(skills_dir: Path | None, value, *, where: str) -> None:
    """The value is a usable skill reference, or raise.

    A plugin reference is accepted without a lookup: whether the agent's
    installation has it is not knowable here, and the injected text tells the
    agent to stop with `needs_context` if it cannot load it.
    """
    if not isinstance(value, str) or not value:
        raise SkillError(f"{where}: 'skill' must be a non-empty string")
    if is_plugin_ref(value):
        return
    _local_path(skills_dir, _own_name(value), where)


def read(skills_dir: Path | None, value: str) -> str:
    """The method text to inject. Called at dispatch, after `validate`."""
    if is_plugin_ref(value):
        return PLUGIN_PROMPT.format(ref=value)
    path = _local_path(skills_dir, _own_name(value), "skill")
    try:
        return path.read_text()
    except (OSError, ValueError) as exc:
        raise SkillError(f"skill: cannot read {value!r} at {path}: {exc}") from exc
