"""A chain read as a stored snapshot is: the only way a `group` node validates
(chain revision makes one; a chain file may not declare it)."""

import yaml

from kraft.policy import FROZEN
from kraft.templates.models import Chain, ResolvedChain


def v1_frozen(nodes: list[dict] | str, *, repo=None, **kw):
    """A `ResolvedChain` over `nodes` (authored V1 node mappings, or the same list
    as YAML text), or with `repo` the `MaterializedChain` bound to that repository
    (`kw`: `v1_chain`'s)."""
    if isinstance(nodes, str):
        nodes = yaml.safe_load(nodes)
    resolved = ResolvedChain.from_chain(
        Chain.model_validate({"id": "t", "nodes": nodes}, context=FROZEN)
    )
    if repo is None:
        return resolved
    from support.harness import v1_chain

    return v1_chain(resolved, repo=repo, **kw)
