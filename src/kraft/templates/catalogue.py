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
            # A child add-on's entry reads its parent's by the parent's name.
            definitions = {
                name: dict(library.public(library.component(namespace, name).data))
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
                    "addon": addon_view(library, id),
                }
            )
    return listed


def addon_view(library: TemplateLibrary, name: str, *, marks: bool = True) -> dict | None:
    """`{id, version, repo, inheritance}` of the add-on a chain or component
    comes from; None for the instance's own. What a screen badges and refuses
    to edit. `name` is a chain id or a component id (`tasks.release:base`),
    which tells a chain and a component of one name apart; `inheritance` is
    `inheritance_view`'s. `marks=False` leaves `inheritance` out, for an
    entry that is neither (an agent profile)."""
    addon = library.addon_of(name)
    if addon is None:
        return None
    view = {"id": addon.id, "version": addon.version, "repo": addon.repo_id}
    if marks:
        view["inheritance"] = inheritance_view(library, name)
    return view


def inheritance_view(library: TemplateLibrary, name: str) -> dict | None:
    """`TemplateLibrary.inheritance(name)` as the API shows it:
    `{origin, parent: {id, namespace}, author: {id, version}}`, the parent
    by the namespace this instance serves it under. None for an entry that
    is not a child add-on's."""
    mark = library.inheritance(name)
    if mark is None:
        return None
    return {
        "origin": mark.origin,
        "parent": {"id": mark.parent.id, "namespace": mark.parent.namespace},
        "author": {"id": mark.author.id, "version": mark.author.version},
    }


def node_inheritance(library: TemplateLibrary, chain_id: str) -> dict[str, dict]:
    """Per node of a chain served under a child add-on, the component (a
    task, step or node, or a steering profile a task selects) of that same
    namespace it is built from that the child overrides or adds:
    `{node: {origin, entry}}`, `overridden` before `added` and the first in
    sorted order between equals. A node built only from inherited components
    is absent, and a chain that is not a child add-on's gives `{}`."""
    top = library.addon_of(chain_id)
    if top is None or top.parent is None:
        return {}
    marked: dict[str, dict] = {}
    for node, refs in library.references(chain_id).items():
        for ref in sorted(refs):
            entry = ref.partition(".")[2]
            if entry.partition(":")[0] != top.namespace:
                continue
            mark = library.inheritance(ref)
            if mark is None or mark.origin == "inherited":
                continue
            if node not in marked or (
                mark.origin == "overridden" and marked[node]["origin"] == "added"
            ):
                marked[node] = {"origin": mark.origin, "entry": ref}
    return marked


def read_only_message(
    library: TemplateLibrary, name: str, section: str | None = None
) -> str | None:
    """Why `name` cannot be saved, when it is an add-on's; None for a local
    name. An entry a child add-on inherits names the ancestor that wrote it
    too (`TemplateLibrary.authored_by`, which reads `section`)."""
    addon = library.addon_of(name)
    if addon is None:
        return None
    if addon.repo_id is not None:
        return f"{name} comes from {addon.repo_id}'s repo library; {EDIT_IN_ITS_REPO}"
    author = library.authored_by(name, section)
    if author is not None and author.id != addon.id:
        return (
            f"{name} comes from add-on {author.id} through {addon.id}; "
            "extend it or copy it to your library"
        )
    return f"{name} comes from add-on {addon.id}; extend it or copy it to your library"


#: Where a repo library's content is changed: never copied into the instance's
#: own library, which would make an instance-wide chain out of one repository's.
EDIT_IN_ITS_REPO = "edit it in that repository's .kraft/"


def repo_library_copy(library: TemplateLibrary, name: str) -> str | None:
    """Why `name` may not be copied into the instance's own library: it is a
    repo library's, visible only to that repository's items. None otherwise."""
    from kraft.addons.load import scope_message

    addon = library.addon_of(name)
    if addon is None or addon.repo_id is None:
        return None
    return f"{scope_message(name, addon.repo_id)}; {EDIT_IN_ITS_REPO}"


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
