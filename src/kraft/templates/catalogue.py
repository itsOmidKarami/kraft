"""The template library as a list of its components (Kraft-6xkkm): each one
`library.yaml` declares, as its author wrote it, with the chains that use it
and the lint issues that name it. What `GET /templates/library` and
`kraft admin templates library` show; built from a loaded `TemplateLibrary`
and its own `lint`, never a second parse."""

from __future__ import annotations

import re

from kraft.templates.library import Namespace, TemplateIssue, TemplateLibrary


def component_id(namespace: Namespace, name: str) -> str:
    """`tasks.implementer`: the section and the name, which is also how a lint
    issue names a component its failure was inherited from (`ComponentSource`)."""
    return f"{namespace.value}.{name}"


def components(library: TemplateLibrary, issues: list[TemplateIssue]) -> list[dict]:
    """Every component, section by section in `library.yaml`'s order. `issues`
    is the library's `lint`; an issue belongs to each component it names."""
    used_by: dict[str, set[str]] = {}
    for chain in library.chain_ids:
        for refs in library.references(chain).values():
            for ref in refs:
                used_by.setdefault(ref, set()).add(chain)

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
                    "issues": [i for i in issues if named.search(i.message)],
                }
            )
    return listed


def find(listed: list[dict], ref: str) -> dict | None:
    """The component `ref` names: its id, or a bare name no other section
    shares."""
    exact = [c for c in listed if c["id"] == ref]
    by_name = [c for c in listed if c["name"] == ref]
    return (exact or (by_name if len(by_name) == 1 else [None]))[0]
