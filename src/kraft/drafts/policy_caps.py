"""The scopes below policy that set a cap, for the Policy page: its "set below
policy" column (`below_for`, in the policy draft's resolve) and a chain's
effective caps under the draft (`chain_scopes`, the "Preview on a chain" read).

Both are derived from the published library's chains; the policy draft changes
only the instance policy they are held against."""

from __future__ import annotations

import copy

from kraft.cap_levels import LEVEL_OF, SCOPE_CAP_FIELDS
from kraft.drafts import authored, resolve
from kraft.policy import InstancePolicy, PolicyError
from kraft.templates.library import TemplateLibraryError
from kraft.templates.models import _scoped

#: A source that is a layer below policy, not the policy's own value.
_BELOW = ("chain", "library:")


def _own(source: str) -> bool:
    return source.startswith(_BELOW)


def _cap(value, source: str, bound: tuple[str, float] | None) -> dict:
    return {
        "value": value,
        "source": source,
        "maximum": {"value": bound[1], "level": bound[0]} if bound else None,
        "exceeds": value is not None and bound is not None and value > bound[1],
    }


def chain_scopes(st, chain_id: str, instance: InstancePolicy) -> list[dict] | None:
    """Per scope of the published chain (the chain itself, then every node,
    step and task; a gate waits for a person and has none), each cap with the
    value it runs under, the layer that set it (`chain`, `library:<section>.<name>`,
    `policy`, `default`) and the maximum bounding it at its level. `None` for a
    chain that does not resolve."""
    library = getattr(st, "library", None)
    if library is None:
        return None
    try:
        resolved = library.resolve_chain(chain_id)
        data = authored.normalise(
            copy.deepcopy(authored.parse(library.chain_file(chain_id).read_text()))
        )
    except (TemplateLibraryError, OSError):
        return None
    sources = resolve._sources(data, resolve._library_model(st, {}), resolved, instance)

    base = instance
    own = resolved.chain.policy
    try:
        if own is not None:
            base = instance.apply_template_override(own)
    except PolicyError:
        base = instance
    out = [
        {
            "path": chain_id,
            "kind": "chain",
            "level": "work_item",
            "caps": {
                cap: _cap(
                    getattr(own, cap, None),
                    "chain" if getattr(own, cap, None) is not None else "default",
                    instance.maxima.nearest("work_item", cap),
                )
                for cap in SCOPE_CAP_FIELDS
            },
        }
    ]
    for path, kind, scope in resolved.cap_scopes():
        if kind == "gate":
            continue
        level = LEVEL_OF[kind]
        try:
            effective = _scoped(scope, base)
        except PolicyError:
            effective = None  # a scope the draft's maximum refuses: its own values still show
        fields = sources.get(path, {})
        caps = {}
        for cap in SCOPE_CAP_FIELDS:
            set_here = fields.get(f"policy.{cap}")
            if set_here is not None and _own(set_here["source"]):
                value, source = set_here["value"], set_here["source"]
            else:
                value = getattr(effective, cap, None)
                source = "policy" if value is not None else "default"
            caps[cap] = _cap(value, source, instance.maxima.nearest(level, cap))
        out.append({"path": path, "kind": kind, "level": level, "caps": caps})
    return out


def below_for(st, instance: InstancePolicy) -> dict[tuple[str, str], list[dict]]:
    """`{(level, cap): [{layer, chain, path, via, value, exceeds}]}`: every
    chain scope, in the published library, that sets `cap` itself, `layer` being
    `chain` (the chain file) or `library` (a component it extends, named in
    `via`), `exceeds` against the draft's maximum at that level."""
    library = getattr(st, "library", None)
    out: dict[tuple[str, str], list[dict]] = {}
    for chain_id in library.chain_ids if library is not None else ():
        for scope in chain_scopes(st, chain_id, instance) or ():
            for cap, c in scope["caps"].items():
                if not _own(c["source"]):
                    continue
                library_layer = c["source"].startswith("library:")
                out.setdefault((scope["level"], cap), []).append(
                    {
                        "layer": "library" if library_layer else "chain",
                        "chain": chain_id,
                        "path": scope["path"],
                        "via": c["source"] if library_layer else None,
                        "value": c["value"],
                        "exceeds": c["exceeds"],
                    }
                )
    return out
