"""The template library as a list of its components (Kraft-6xkkm): each one
`library.yaml` declares, as its author wrote it, with the chains that use it
(and the path in each that does) and the lint issues that name it. What `GET /templates/library` and
`kraft admin templates library` show; built from a loaded `TemplateLibrary`
and its own `lint`, never a second parse."""

from __future__ import annotations

import re

from kraft.policy import GRANT_SUMMARIES, GRANTS
from kraft.templates.library import Namespace, TemplateIssue, TemplateLibrary
from kraft.templates.models import AgentInput, BuiltinAction, ForgeAction


def component_id(namespace: Namespace, name: str) -> str:
    """`tasks.implementer`: the section and the name, which is also how a lint
    issue names a component its failure was inherited from (`ComponentSource`)."""
    return f"{namespace.value}.{name}"


def components(library: TemplateLibrary, issues: list[TemplateIssue]) -> list[dict]:
    """Every component, section by section in `library.yaml`'s order. `issues`
    is the library's `lint`; an issue belongs to each component it names."""
    used_by: dict[str, set[str]] = {}
    paths: dict[str, list[dict]] = {}
    for chain in library.chain_ids:
        for refs in library.references(chain).values():
            for ref in refs:
                used_by.setdefault(ref, set()).add(chain)
        for ref, uses in library.usages(chain).items():
            paths.setdefault(ref, []).extend({"chain": chain, **use} for use in uses)

    listed = []
    for namespace in Namespace:
        if namespace is Namespace.STEERING:
            definitions = {
                name: profile.model_dump(mode="json", exclude_unset=True)
                for name, profile in library.steering.items()
            }
        else:
            definitions = {
                name: dict(library.component(namespace, name).data)
                for name in library.component_names(namespace)
            }
        for name, definition in definitions.items():
            id = component_id(namespace, name)
            # Not a prefix of a longer name: `tasks.foo` is not `tasks.foo-bar`.
            named = re.compile(rf"(?<![\w-]){re.escape(id)}(?![\w-])")
            listed.append(
                {
                    "id": id,
                    "kind": namespace.value,
                    "name": name,
                    "definition": definition,
                    "used_by": sorted(used_by.get(id, ())),
                    "used_by_paths": paths.get(id, []),
                    "issues": [i for i in issues if named.search(i.message)],
                    "plugin": plugin_view(library, name),
                }
            )
    return listed


def plugin_view(library: TemplateLibrary, name: str) -> dict | None:
    """`{id, version}` of the plugin a chain or component comes from; None for
    the instance's own. What a screen badges and refuses to edit."""
    plugin = library.plugin_of(name)
    return None if plugin is None else {"id": plugin.id, "version": plugin.version}


def read_only_message(library: TemplateLibrary, name: str) -> str | None:
    """Why `name` cannot be saved, when it is a plugin's; None for a local name."""
    plugin = library.plugin_of(name)
    if plugin is None:
        return None
    return f"{name} comes from plugin {plugin.id}; extend it or copy it to your library"


class AmbiguousName(LookupError):
    """A bare name more than one section declares: `ids` are each one's id."""

    def __init__(self, ref: str, ids: list[str]):
        super().__init__(f"{ref!r} is ambiguous: {', '.join(ids)}")
        self.ids = ids


def find(listed: list[dict], ref: str) -> dict | None:
    """The component `ref` names: its id, or a bare name no other section
    shares. `AmbiguousName` for a bare name two sections share, rather than
    the None that reads as "no such component"."""
    exact = [c for c in listed if c["id"] == ref]
    if exact:
        return exact[0]
    by_name = [c for c in listed if c["name"] == ref]
    if len(by_name) > 1:
        raise AmbiguousName(ref, [c["id"] for c in by_name])
    return by_name[0] if by_name else None


def choices() -> dict[str, list[dict]]:
    """The closed sets a template field takes its values from, by field: a
    builtin task's `ref`, a forge task's `target` (and whether it `waits`), an
    agent task's `inputs`, and the `grants` a policy layer names. Each value
    carries a one-line `summary`. The editors offer these instead of a free
    text field, so the vocabulary has one owner: the schema here."""
    return {
        "ref": [{"value": a.value, "summary": a.summary} for a in BuiltinAction],
        "target": [{"value": t.value, "summary": t.summary, "waits": t.waits} for t in ForgeAction],
        "inputs": [{"value": i.value, "summary": i.summary} for i in AgentInput],
        "grants": [{"value": g, "summary": GRANT_SUMMARIES[g]} for g in GRANTS],
    }
