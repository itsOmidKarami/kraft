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

from collections.abc import Mapping
from pathlib import Path

#: Method files that ship with Kraft. Read from the package, never from
#: `$KRAFT_HOME/config/`, because `cli.seed_home` copies templates once and
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


def _is_file(path: Path) -> bool:
    """`Path.is_file()`, but a name too long for the filesystem is "no such file"
    on every Python: 3.14 answers False, 3.12 and 3.13 raise ENAMETOOLONG."""
    try:
        return path.is_file()
    except OSError:
        return False


def _bare(name: str, where: str) -> None:
    if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
        raise SkillError(
            f"{where}: skill name {name!r} must be a bare directory name — Kraft "
            "never reads a method file from outside its own skills directories"
        )


def _local_path(skills_dir: Path | None, name: str, where: str) -> Path:
    _bare(name, where)
    if skills_dir is not None:
        overlay = Path(skills_dir) / name / "SKILL.md"
        if _is_file(overlay):
            return overlay
    bundled = BUNDLED / name / "SKILL.md"
    if _is_file(bundled):
        return bundled
    looked = [str(bundled)]
    if skills_dir is not None:
        looked.insert(0, str(Path(skills_dir) / name / "SKILL.md"))
    raise SkillError(f"{where}: skill {name!r} not found at {' or '.join(looked)}")


def path_for(skills_dir: Path | None, name: str, *, where: str) -> Path:
    """The file a bare or `kraft:` skill name resolves to, or raise. Not for
    another plugin's refs."""
    return _local_path(skills_dir, _own_name(name), where)


def _plugin_path(
    plugin_dirs: Mapping[str, Path | None] | None, value: str, where: str
) -> Path | None:
    """The file `<namespace>:<name>` names when the namespace is a loaded Kraft
    plugin's, read from that plugin's store; None when it is not, which leaves
    the reference another tool's."""
    qualifier, _, name = value.partition(":")
    if qualifier not in (plugin_dirs or {}):
        return None
    root = (plugin_dirs or {})[qualifier]
    if root is None:
        raise SkillError(
            f"{where}: plugin {qualifier!r} is installed but not loaded, so its skill "
            f"{name!r} cannot be read; `kraft admin health` says why"
        )
    _bare(name, where)
    path = Path(root) / "skills" / name / "SKILL.md"
    if not _is_file(path):
        raise SkillError(
            f"{where}: plugin {qualifier!r} has no skill {name!r} at {path}; a method Kraft "
            f"ships is written kraft:{name}"
        )
    return path


def validate(
    skills_dir: Path | None,
    value,
    *,
    where: str,
    plugin_dirs: Mapping[str, Path | None] | None = None,
) -> None:
    """The value is a usable skill reference, or raise.

    Another tool's plugin reference is accepted without a lookup: whether the
    agent's installation has it is not knowable here, and the injected text
    tells the agent to stop with `needs_context` if it cannot load it. A
    loaded Kraft plugin's is looked up in its store (`plugin_dirs`, namespace
    to directory).
    """
    if not isinstance(value, str) or not value:
        raise SkillError(f"{where}: 'skill' must be a non-empty string")
    if is_plugin_ref(value):
        _plugin_path(plugin_dirs, value, where)
        return
    _local_path(skills_dir, _own_name(value), where)


def read(
    skills_dir: Path | None, value: str, plugin_dirs: Mapping[str, Path | None] | None = None
) -> str:
    """The method text to inject. Called at dispatch, after `validate`."""
    path = _plugin_path(plugin_dirs, value, "skill") if is_plugin_ref(value) else None
    if path is None and is_plugin_ref(value):
        return PLUGIN_PROMPT.format(ref=value)
    path = path or _local_path(skills_dir, _own_name(value), "skill")
    try:
        return path.read_text()
    except (OSError, ValueError) as exc:
        raise SkillError(f"skill: cannot read {value!r} at {path}: {exc}") from exc
