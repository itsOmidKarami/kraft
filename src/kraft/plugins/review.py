"""What an install or update of one plugin would change, said before anything
is written.

A review compares the candidate's files with the locked store's (or with
nothing, on install). It leads with **reach**: what can change how far a run
goes, which is also what `auto_update` holds for a person. The rest is
**content**. Each resolved chain is flattened to `path -> value` facts keyed
by component ids, so a diff is a diff of facts and a new field in the chain
schema shows up as content without a line of code here.
"""

from __future__ import annotations

import difflib
import json
import re
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft.config import bounded_yaml
from kraft.plugins import manifest
from kraft.plugins.fetch import Extracted
from kraft.plugins.load import InstalledPlugin
from kraft.plugins.update import LIMITS
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

_MERGES = ("mr.merge", "mr.mark_ready")
_GATE_FIELDS = ("chain_finalized", "skippable", "reject_to", "artifact_required")
_ROUTE = ("harness", "model", "effort", "profile", "fallback")
_INPUTS = ("inputs",)
_NODE = re.compile(r"\.nodes\[([^\]]+)\]")


@dataclass(frozen=True)
class Review:
    """One plugin's change. `old_version` is None on an install."""

    plugin_id: str
    old_version: str | None
    new_version: str
    #: What can change how far a run reaches. Any line here holds `auto_update`.
    reach: tuple[str, ...] = ()
    content: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.reach or self.content) or self.old_version != self.new_version


def precedence(version: str) -> tuple:
    """A sort key for a SemVer 2.0.0 version without build metadata: a
    pre-release sorts below its own release, numeric identifiers below
    alphanumeric ones, and a longer pre-release above its own prefix."""
    core, _, pre = version.partition("-")
    numbers = tuple(int(part) for part in core.split("."))
    if not pre:
        return (numbers, (1,))
    identifiers = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split(".")
    )
    return (numbers, (0, identifiers))


def escape(text: str) -> str:
    """`text` with every character a reader cannot see written out. Newline
    and tab are kept."""
    return "".join(
        f"\\u{{{ord(ch):04X}}}"
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf")
        else ch
        for ch in text
    )


def render(review: Review) -> str:
    """The review as the terminal, `--check` and `--json` all print it."""
    was = f"{review.old_version} -> " if review.old_version else "new, "
    lines = [f"{review.plugin_id} {was}{review.new_version}"]
    for title, entries in (("Reach", review.reach), ("Content", review.content)):
        if entries:
            lines.append(f"{title}:")
            lines += [f"  - {entry}" for entry in entries]
    if not review.changed:
        lines.append("  up to date")
    return escape("\n".join(lines))


def _flatten(value: object, at: str, out: dict[str, object], lists: dict[str, list[str]]) -> None:
    """Facts go to `out`; the order of each id-keyed list, which a fact keyed
    by id cannot show, goes to `lists`."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key != "id":
                _flatten(item, f"{at}.{key}", out, lists)
    elif (
        isinstance(value, list)
        and value
        and all(isinstance(item, Mapping) and "id" in item for item in value)
    ):
        lists[at] = [str(item["id"]) for item in value]
        for item in value:
            _flatten(item, f"{at}[{item['id']}]", out, lists)
    elif value is not None and value != {} and (value != [] or at.endswith(".fallback")):
        # `fallback: []` says "none"; absent, the task takes its profile's.
        out[at] = value


def _yaml(extracted: Extracted, rel: str) -> dict:
    entry = extracted.files.get(rel)
    if entry is None:
        return {}
    try:
        data = bounded_yaml(entry[1].decode(), [20_000])
    except (ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(data: dict, name: str) -> dict:
    section = data.get(name)
    return section if isinstance(section, dict) else {}


@dataclass(frozen=True)
class _Facts:
    version: str
    requires: dict
    #: Every resolved chain, flattened; and each chain's gates in node order.
    chains: dict[str, object]
    lists: dict[str, list[str]]
    order: dict[str, list[tuple[str, str]]]
    components: dict[str, object]
    steering: dict[str, object]
    skills: dict[str, str]
    profiles: dict[str, object]


def _chain_facts(
    loaded: TemplateLibrary,
    chain_ids: Iterable[str],
    chains: dict[str, object],
    lists: dict[str, list[str]],
    order: dict[str, list[tuple[str, str]]],
) -> None:
    """Each of `chain_ids` that resolves in `loaded`, flattened into the three maps."""
    for chain_id in chain_ids:
        try:
            dumped = loaded.resolve_chain(chain_id).chain.model_dump(mode="json")
        except TemplateLibraryError:
            continue
        _flatten(dumped, chain_id, chains, lists)
        order[chain_id] = [(node["id"], node["kind"]) for node in dumped["nodes"]]


def _facts(extracted: Extracted | None) -> _Facts:
    if extracted is None:
        return _Facts("", {}, {}, {}, {}, {}, {}, {}, {})
    declared = json.loads(extracted.files[manifest.PLUGIN_JSON][1])
    name = declared.get("name", "plugin")
    library = _yaml(extracted, "library.yaml")
    chains: dict[str, object] = {}
    lists: dict[str, list[str]] = {}
    order: dict[str, list[tuple[str, str]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, (_mode, data) in extracted.files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        plugin = InstalledPlugin(f"{name}@review", name, name, "", root)
        try:
            loaded = TemplateLibrary.from_mappings(
                {}, (), library_path=root / "local.yaml", plugins=[plugin]
            )
        except TemplateLibraryError:
            loaded = None  # lint refuses it; there is nothing to compare
        if loaded is not None:
            _chain_facts(loaded, loaded.chain_ids, chains, lists, order)
    return _Facts(
        version=str(declared.get("version", "")),
        requires=declared.get("requires") if isinstance(declared.get("requires"), dict) else {},
        chains=chains,
        lists=lists,
        order=order,
        components={
            f"{section}.{key}": body
            for section in ("tasks", "steps", "nodes")
            for key, body in _section(library, section).items()
        },
        steering=_section(library, "steering"),
        skills={
            rel.split("/")[1]: data.decode()
            for rel, (_mode, data) in extracted.files.items()
            if rel.startswith("skills/")
        },
        profiles=_section(_yaml(extracted, "profiles.yaml"), "profiles"),
    )


def _how(old: object, new: object) -> str:
    if old is None:
        return f"set to {new!r}"
    if new is None:
        return f"removed (was {old!r})"
    return f"{old!r} -> {new!r}"


def _chain_change(
    path: str, old: object, new: object, was: _Facts, now: _Facts, name: str
) -> tuple[str, str]:
    """`(section, line)` for one changed fact of a resolved chain."""
    where, _, last = path.rpartition(".")
    if ".auto_review" in path:
        return "reach", f"{where}: a gate's own agent review changed: {last} {_how(old, new)}"
    if last in _GATE_FIELDS:
        return "reach", f"{where}: {last} {_how(old, new)}"
    if last == "kind":
        if old == "gate":  # the fact changed, so `new` is not a gate
            return "reach", f"{where}: gate removed{'' if new is None else f', now {new}'}"
        return "content", f"{where}: {'removed' if new is None else f'added ({new})'}"
    if last == "target":
        chain_id = path.split(".nodes[", 1)[0]
        used = {
            v
            for p, v in was.chains.items()
            if p.startswith(f"{chain_id}.") and p.endswith(".target")
        }
        if new in _MERGES:
            node = _NODE.search(path)
            before = []
            for node_id, kind in now.order.get(chain_id, ()):
                if node is not None and node_id == node.group(1):
                    break
                before.append(kind)
            gated = "a gate comes before it" if "gate" in before else "NO gate comes before it"
            return "reach", f"{where}: {new} step {_how(old, new)}; {gated}"
        if new is not None and new not in used:
            return "reach", f"{where}: forge target {new!r}, which this chain did not use before"
        if new is None:
            # A wait for CI or for a person's approval is a forge step too.
            return "reach", f"{where}: forge step {old!r} removed"
        return "content", f"{where}: forge target {_how(old, new)}"
    if last == "read_only" and old and not new:
        return "reach", f"{where}: no longer read-only"
    if last == "scope" and old is not None:
        # `each_repository` runs the task once more per repository.
        return "reach", f"{where}: scope {_how(old, new)}"
    if (".policy." in path or ".fix_loop." in path) and last in LIMITS:
        if last == "deny_tools":
            dropped = sorted(set(old or ()) - set(new or ()))
            if dropped:
                return "reach", f"{where}: deny_tools no longer denies {dropped}"
            return "content", f"{where}: deny_tools {_how(old, new)}"
        lowered = isinstance(old, int | float) and isinstance(new, int | float) and new < old
        if lowered:
            return "content", f"{where}: {last} lowered, {_how(old, new)}"
        word = "removed" if new is None else "raised" if old is not None else "set"
        return "reach", f"{where}: limit {last} {word}, {_how(old, new)}"
    if last in _ROUTE:
        return "reach", f"{where}: {last} {_how(old, new)}"
    if last == "prompt":
        return "content", f"{where}: prompt {'new' if old is None else 'changed'}:\n{new or ''}"
    if last == "skill":
        qualifier = new.split(":", 1)[0] if isinstance(new, str) and ":" in new else None
        if qualifier not in (None, name, "kraft"):
            return "content", f"{where}: new plugin skill reference {new!r}, handed to the agent"
        return "content", f"{where}: skill {_how(old, new)}"
    if last in _INPUTS or last == "steering":
        return "content", f"{where}: {last} {_how(old, new)}"
    return "content", f"{path}: {_how(old, new)}"


def _reordered(path: str, before: list[str], after: list[str]) -> tuple[str, str]:
    """`(section, line)` for an id-keyed list whose common entries changed
    order. A moved node runs its work on the other side of a gate or a merge,
    and a moved step on the other side of a wait or a check in its node; the
    tasks of one step run together."""
    section = "reach" if path.endswith((".nodes", ".steps")) else "content"
    return section, f"{path}: order changed, {before} -> {after}"


def _chain_diff(was: _Facts, now: _Facts, name: str, reach: list[str], content: list[str]) -> None:
    """Every changed fact and every reordered list of the resolved chains."""
    for path in sorted(was.chains.keys() | now.chains.keys()):
        before, after = was.chains.get(path), now.chains.get(path)
        if before != after:
            section, line = _chain_change(path, before, after, was, now, name)
            (reach if section == "reach" else content).append(line)
    for path in sorted(was.lists.keys() & now.lists.keys()):
        common = set(was.lists[path]) & set(now.lists[path])
        before = [i for i in was.lists[path] if i in common]
        after = [i for i in now.lists[path] if i in common]
        if before != after:
            section, line = _reordered(path, before, after)
            (reach if section == "reach" else content).append(line)


def local_changes(
    before: TemplateLibrary | None, after: TemplateLibrary, namespace: str
) -> tuple[list[str], list[str]]:
    """`(reach, content)` for the instance's own chains: what each one that
    resolves through the plugin under `namespace` would run differently once
    `after` replaces `before`. A chain the plugin does not reach has no line."""
    sides = []
    for loaded in (before, after):
        facts = _Facts("", {}, {}, {}, {}, {}, {}, {}, {})
        if loaded is not None:
            local = [chain_id for chain_id in loaded.chain_ids if ":" not in chain_id]
            _chain_facts(loaded, local, facts.chains, facts.lists, facts.order)
        sides.append(facts)
    reach: list[str] = []
    content: list[str] = []
    _chain_diff(sides[0], sides[1], namespace, reach, content)
    return reach, content


def review(plugin_id: str, old: Extracted | None, new: Extracted) -> Review:
    """What replacing `old` (None on install) with `new` would change."""
    was, now = _facts(old), _facts(new)
    name = plugin_id.split("@", 1)[0]
    reach: list[str] = []
    content: list[str] = []
    if old is not None:
        if precedence(now.version) < precedence(was.version):
            reach.append(f"downgrade: {was.version} -> {now.version}")
        if "-" in now.version and "-" not in was.version:
            reach.append(f"moves onto a pre-release: {now.version}")
        if was.requires != now.requires:
            reach.append(f"requires changed: {was.requires} -> {now.requires}")
    _chain_diff(was, now, name, reach, content)
    for label, before_map, after_map in (
        ("component", was.components, now.components),
        ("steering", was.steering, now.steering),
        ("agent profile", was.profiles, now.profiles),
    ):
        for key in sorted(before_map.keys() | after_map.keys()):
            if before_map.get(key) != after_map.get(key):
                state = (
                    "added"
                    if key not in before_map
                    else "removed"
                    if key not in after_map
                    else "changed"
                )
                detail = f": {_how(before_map.get(key), after_map.get(key))}"
                # A task keeps `profile: fast` while what `fast` runs on changes.
                rerouted = label == "agent profile" and state == "changed"
                (reach if rerouted else content).append(
                    f"{label} {key} {state}{detail if label != 'component' else ''}"
                )
    for key in sorted(was.skills.keys() | now.skills.keys()):
        before_text, after_text = was.skills.get(key, ""), now.skills.get(key, "")
        if before_text != after_text:
            diff = "\n".join(
                difflib.unified_diff(
                    before_text.splitlines(),
                    after_text.splitlines(),
                    f"skills/{key}/SKILL.md (locked)",
                    f"skills/{key}/SKILL.md",
                    lineterm="",
                )
            )
            content.append(f"skill {key} changed:\n{diff}")
    return Review(
        plugin_id=plugin_id,
        old_version=was.version if old is not None else None,
        new_version=now.version,
        reach=tuple(reach),
        content=tuple(content),
    )
